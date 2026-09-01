# -*- coding: utf-8 -*-
"""方案C 严格方案挖掘：网格搜索腿级/组合级约束，找出高命中严格配置，再设计降级链（只读）。

约束维度：
  - 半全场腿：p_hat>=0.35 / 赔率<=3.0
  - 胜平负腿：仅ambiguous（剔除冷门/兜底）/ 赔率<2.5
  - 组合：异联赛（hafu与had不同联赛）/ 串关赔率[4,10]
输出：
  1) 各配置单串表现（出串天数/命中/p_hit/均赔/ROI）
  2) 降级链评估：strict(最高命中) → loose → fallback 覆盖全月
"""
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.api.market_flow import _market_flow_query, _matchday_date
from app.db.database import async_session

DIR_ZH = {"home": "主", "draw": "平", "away": "客"}
HAFU_ZH = {"hh": "胜胜", "hd": "胜平", "ha": "胜负", "dh": "平胜", "dd": "平平",
           "da": "平负", "ah": "负胜", "ad": "负平", "aa": "负负"}
_DIR_KEY = {"home": "h", "draw": "d", "away": "a"}
MIN_ODDS, MAX_ODDS = 4.0, 10.0


def _parse_hafu(raw):
    if raw is None:
        return {}
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return {}
    if not isinstance(data, dict):
        return {}
    return {k: float(v) for k, v in data.items() if v and float(v) > 0}


def _implied(odds_map):
    inv = {k: 1.0 / v for k, v in odds_map.items() if v and v > 0}
    if not inv:
        return None
    tot = sum(inv.values())
    return {k: v / tot for k, v in inv.items()}


def _half_dir(it):
    hh, ha = it.get("half_home_score"), it.get("half_away_score")
    if not isinstance(hh, int) or not isinstance(ha, int):
        return None
    return "home" if hh > ha else "away" if hh < ha else "draw"


def _actual_hafu_key(it):
    hd = _half_dir(it)
    fo = it.get("actual_outcome")
    if hd is None or fo not in ("home", "draw", "away"):
        return None
    return _DIR_KEY[hd] + _DIR_KEY[fo]


def _same_match(a, b):
    if a.get("match_num") and b.get("match_num"):
        return a["match_num"] == b["match_num"]
    return a.get("kickoff_time") == b.get("kickoff_time")


def _pl_odds(legs):
    o = 1.0
    for l in legs:
        o *= l["odds"]
    return o


def _pl_phat(legs):
    p = 1.0
    for l in legs:
        p *= l["p_hat"]
    return p


def _pl_hit(legs):
    if not all(l["hit"] is not None for l in legs):
        return None
    return all(l["hit"] for l in legs)


async def main():
    _win = os.getenv("WIN", "aug")
    if _win == "jul":
        start = datetime(2026, 7, 1, 12, 0, 0)
        end = datetime(2026, 8, 1, 12, 0, 0)
    else:
        start = datetime(2026, 8, 1, 12, 0, 0)
        end = datetime(2026, 9, 1, 12, 0, 0)
    async with async_session() as db:
        items, _os, _osm = await _market_flow_query(
            db, start=start, end=end, ou_tier="standard", ou_sm_tier="standard"
        )

        by_day = {}
        for it in items:
            d = by_day.setdefault(
                _matchday_date(it["kickoff_time"], match_num=it.get("match_num")),
                {"hafu": [], "had": []},
            )
            settled = it.get("actual_outcome") is not None
            actual_outcome = it.get("actual_outcome")
            pool = it.get("pool")
            had = it.get("had_odds") or {}
            hafu = _parse_hafu(it.get("hafu_odds"))
            if hafu:
                ip = _implied(hafu)
                if ip:
                    cand = [(k, v) for k, v in hafu.items() if v >= 2.0]
                    if cand:
                        key = max(cand, key=lambda kv: ip.get(kv[0], 0.0))[0]
                        odd = hafu[key]
                        ahk = _actual_hafu_key(it)
                        hit = (ahk == key) if (settled and ahk is not None) else None
                        d["hafu"].append({
                            "match_num": it.get("match_num"),
                            "home_team": it.get("home_team"),
                            "away_team": it.get("away_team"),
                            "league_name": it.get("league_name"),
                            "kickoff_time": it["kickoff_time"],
                            "hafu_key": key,
                            "pick": HAFU_ZH[key],
                            "p_hat": round(ip[key], 4),
                            "odds": round(odd, 4),
                            "hit": hit,
                        })
            if pool in ("ambiguous", "upset"):
                fallback = False
                if pool == "upset":
                    direction = it.get("preferred_outcome")
                    if direction is None:
                        direction = it.get("cold_dir")
                        fallback = True
                    src = "had_upset"
                else:
                    direction = it.get("fav")
                    src = "ambiguous_had"
                if direction and direction in had and had.get(direction) and had[direction] >= 1.8:
                    ip_had = _implied(had) if had else None
                    p = ip_had.get(direction) if ip_had else None
                    d["had"].append({
                        "match_num": it.get("match_num"),
                        "home_team": it.get("home_team"),
                        "away_team": it.get("away_team"),
                        "league_name": it.get("league_name"),
                        "kickoff_time": it["kickoff_time"],
                        "source": src,
                        "pick": DIR_ZH[direction],
                        "pref": direction,
                        "p_hat": round(float(p), 4) if isinstance(p, (int, float)) else round(float(had[direction]), 4),
                        "odds": round(had[direction], 4),
                        "fallback": fallback,
                        "hit": (actual_outcome == direction) if settled else None,
                    })

        days = []
        for matchday, grp in sorted(by_day.items()):
            hafu = sorted(grp["hafu"], key=lambda x: x["p_hat"], reverse=True)
            had = sorted(grp["had"], key=lambda x: x["p_hat"], reverse=True)
            if not hafu or not had:
                continue
            had_normal = [x for x in had if not x.get("fallback")]
            days.append({
                "matchday": matchday,
                "hafu": hafu, "had": had, "had_normal": had_normal,
            })

        def _pick_day(d, cfg):
            """按 cfg 过滤当日腿，返回最优单串 (legs or None)。"""
            hafu = [x for x in d["hafu"] if x["hit"] is not None]
            had = [x for x in d["had"] if x["hit"] is not None]
            # 胜平负腿过滤
            if cfg.get("had_src") == "ambiguous":
                had = [x for x in had if x["source"] == "ambiguous_had"]
            elif cfg.get("had_src") == "normal":
                had = [x for x in had if not x.get("fallback")]
            if cfg.get("had_odds25"):
                had = [x for x in had if x["odds"] < 2.5]
            # 半全场腿过滤
            if cfg.get("hafu_p35"):
                hafu = [x for x in hafu if x["p_hat"] >= 0.35]
            if cfg.get("hafu_odds3"):
                hafu = [x for x in hafu if x["odds"] <= 3.0]
            if cfg.get("hafu_hhaa"):
                hafu = [x for x in hafu if x["hafu_key"] in ("hh", "aa")]
            if not hafu or not had:
                return None
            # 组合
            best = None
            for a in hafu:
                for b in had:
                    if _same_match(a, b):
                        continue
                    if cfg.get("diff_league") and a["league_name"] == b["league_name"]:
                        continue
                    o = _pl_odds([a, b])
                    if cfg.get("in_range") and not (MIN_ODDS <= o <= MAX_ODDS):
                        continue
                    legs = [a, b]
                    if best is None or _pl_phat(legs) > _pl_phat(best):
                        best = legs
            return best

        lines = []

        # ===== 1) 单配置网格 =====
        CONFIGS = [
            ("基线:生产", {}),
            ("A:仅ambiguous", {"had_src": "ambiguous"}),
            ("B:A+异联赛", {"had_src": "ambiguous", "diff_league": True}),
            ("C:A+赔率[4,10]", {"had_src": "ambiguous", "in_range": True}),
            ("D:A+异联赛+[4,10]", {"had_src": "ambiguous", "diff_league": True, "in_range": True}),
            ("E:A+异联赛+[4,10]+p35", {"had_src": "ambiguous", "diff_league": True, "in_range": True, "hafu_p35": True}),
            ("F:A+异联赛+[4,10]+hafu赔率<=3", {"had_src": "ambiguous", "diff_league": True, "in_range": True, "hafu_odds3": True}),
            ("G:异联赛(全来源)", {"diff_league": True}),
            ("H:异联赛+[4,10](全来源)", {"diff_league": True, "in_range": True}),
            ("I:A+胜平负赔率<2.5", {"had_src": "ambiguous", "had_odds25": True}),
            ("J:A+异联赛+[4,10]+胜平负<2.5", {"had_src": "ambiguous", "diff_league": True, "in_range": True, "had_odds25": True}),
        ]
        lines.append(f"===== 方案C 严格方案网格（{_win}） =====")
        results = {}
        for name, cfg in CONFIGS:
            rows = []
            n_days = 0
            for d in days:
                legs = _pick_day(d, cfg)
                if legs is None:
                    continue
                n_days += 1
                h = _pl_hit(legs)
                rows.append((h, _pl_odds(legs)))
            settled = [r for r in rows if r[0] is not None]
            n = len(settled)
            hit = sum(1 for h, _ in settled if h)
            ph = hit / n if n else 0
            av = sum(o for _, o in settled) / n if n else 0
            roi = av * ph - 1 if n else 0
            results[name] = {"n_days": n_days, "n": n, "hit": hit, "ph": ph, "av": av, "roi": roi}
            lines.append(f"{name}: 出串={n_days}天 已结算={n} 命中={hit} p_hit={ph:.4f} 均赔={av:.2f} ROI={roi:+.4f}")

        # ===== 2) 降级链评估 =====
        lines.append("\n===== 降级链评估（strict 优先，逐级放宽） =====")
        CHAIN = [
            ("strict: A+异联赛+[4,10]+p35", {"had_src": "ambiguous", "diff_league": True, "in_range": True, "hafu_p35": True}),
            ("loose1: A+异联赛", {"had_src": "ambiguous", "diff_league": True}),
            ("loose2: A", {"had_src": "ambiguous"}),
            ("fallback: 生产", {}),
        ]
        for chain_name, chain in [
            ("链1 [strict→A]", CHAIN[:2] + [CHAIN[3]]),
            ("链2 [strict→A→生产]", CHAIN[:3] + [CHAIN[3]]),
            ("链3 [strict→生产]", [CHAIN[0]] + [CHAIN[3]]),
        ]:
            rows = []
            n_days = 0
            level_used = {}
            for d in days:
                used = None
                legs = None
                for lv, cfg in chain:
                    legs = _pick_day(d, cfg)
                    if legs is not None:
                        used = lv
                        break
                if legs is None:
                    continue
                n_days += 1
                level_used[used] = level_used.get(used, 0) + 1
                h = _pl_hit(legs)
                rows.append((h, _pl_odds(legs)))
            settled = [r for r in rows if r[0] is not None]
            n = len(settled)
            hit = sum(1 for h, _ in settled if h)
            ph = hit / n if n else 0
            av = sum(o for _, o in settled) / n if n else 0
            roi = av * ph - 1 if n else 0
            lu = " ".join(f"{k.split(':')[0]}={v}" for k, v in sorted(level_used.items()))
            lines.append(f"{chain_name}: 出串={n_days}天 命中={hit}/{n} p_hit={ph:.4f} 均赔={av:.2f} ROI={roi:+.4f} | 层级分布: {lu}")

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_parlay_c_optimize_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out_path)


if __name__ == "__main__":
    asyncio.run(main())
