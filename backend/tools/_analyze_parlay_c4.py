# -*- coding: utf-8 -*-
"""方案C 严格vs基线 逐日对照：验证约束是否真有鉴别力（只读）。

对照每一天：基线组合（无约束 p_hat top-1）、strict 组合（F: amb+异联赛+[4,10]+hafu赔率<=3）、
loose 组合（A: 仅ambiguous）。统计：
  1) strict 能出串的天：基线同天命中 vs strict 组合命中（约束是否有选组合能力）
  2) 降级天（strict 给不出串）：基线命中率（这些天是否真的更差）
  3) 逐日对照表
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
            days.append({
                "matchday": matchday,
                "hafu": hafu, "had": had,
            })

        def _pick(d, had_src=None, diff_league=False, in_range=False, hafu_odds3=False):
            """返回最优单串（组合 p_hat 最高），None 表示给不出。"""
            hafu = [x for x in d["hafu"] if x["hit"] is not None]
            had = [x for x in d["had"] if x["hit"] is not None]
            if had_src == "amb":
                had = [x for x in had if x["source"] == "ambiguous_had"]
            if hafu_odds3:
                hafu = [x for x in hafu if x["odds"] <= 3.0]
            if not hafu or not had:
                return None
            best = None
            for a in hafu:
                for b in had:
                    if _same_match(a, b):
                        continue
                    if diff_league and a["league_name"] == b["league_name"]:
                        continue
                    o = _pl_odds([a, b])
                    if in_range and not (MIN_ODDS <= o <= MAX_ODDS):
                        continue
                    legs = [a, b]
                    if best is None or _pl_phat(legs) > _pl_phat(best):
                        best = legs
            return best

        lines = []
        lines.append(f"===== 方案C 严格vs基线 逐日对照（{_win}） =====")
        lines.append("day | 基线(生产) | strict(F) | 备注")
        rows = []
        for d in days:
            base = _pick(d)
            strict = _pick(d, had_src="amb", diff_league=True, in_range=True, hafu_odds3=True)
            loose = _pick(d, had_src="amb")
            base_hit = _pl_hit(base) if base else None
            strict_hit = _pl_hit(strict) if strict else None
            loose_hit = _pl_hit(loose) if loose else None
            rows.append({
                "matchday": d["matchday"], "base": base, "strict": strict, "loose": loose,
                "base_hit": base_hit, "strict_hit": strict_hit, "loose_hit": loose_hit,
            })
            note = ""
            if strict is None:
                note = "strict无组合"
            elif base_hit is True and strict_hit is False:
                note = "★基线中/strict未中(降级损失)"
            elif base_hit is False and strict_hit is True:
                note = "★基线未中/strict中(严格收益)"
            lines.append(f"{d['matchday']} | 基线:{'中' if base_hit else '未中' if base_hit is False else '-'}"
                         f" | strict:{'中' if strict_hit else '未中' if strict_hit is False else '无组合'}"
                         f" | {note}")

        # 统计
        lines.append("\n===== 统计 =====")
        strict_days = [r for r in rows if r["strict"] is not None]
        drop_days = [r for r in rows if r["strict"] is None]
        gain = sum(1 for r in strict_days if r["base_hit"] is False and r["strict_hit"] is True)
        loss = sum(1 for r in strict_days if r["base_hit"] is True and r["strict_hit"] is False)
        both = sum(1 for r in strict_days if r["base_hit"] is True and r["strict_hit"] is True)
        neither = sum(1 for r in strict_days if r["base_hit"] is False and r["strict_hit"] is False)
        lines.append(f"strict能出串 {len(strict_days)} 天: 基线中&strict中={both} 基线中&strict未中={loss} 基线未中&strict中={gain} 双未中={neither}")
        lines.append(f"  → strict 替换基线的净收益 = {gain - loss} 天")
        # strict 天里基线命中率 vs strict 命中率
        b_hit = sum(1 for r in strict_days if r["base_hit"] is True)
        s_hit = sum(1 for r in strict_days if r["strict_hit"] is True)
        lines.append(f"strict天内: 基线命中={b_hit}/{len(strict_days)}={b_hit/len(strict_days):.3f}  strict命中={s_hit}/{len(strict_days)}={s_hit/len(strict_days):.3f}")
        # 降级天基线命中率
        if drop_days:
            db_hit = sum(1 for r in drop_days if r["base_hit"] is True)
            lines.append(f"strict无组合的 {len(drop_days)} 天: 基线命中={db_hit}/{len(drop_days)}={db_hit/len(drop_days):.3f}")
        # 全月对照
        b_all = sum(1 for r in rows if r["base_hit"] is True)
        chain = 0
        for r in rows:
            if r["strict"] is not None and r["strict_hit"] is True:
                chain += 1
            elif r["strict"] is None and r["base_hit"] is True:
                chain += 1
        lines.append(f"全月: 基线命中={b_all}/{len(rows)}  降级链[strict→fallback]命中={chain}/{len(rows)}")

        # loose(A: 仅ambiguous) vs 基线 对照
        lines.append("\n===== loose(A:仅ambiguous) vs 基线 =====")
        a_days = [r for r in rows if r["loose"] is not None]
        a_drop = [r for r in rows if r["loose"] is None]
        a_gain = sum(1 for r in a_days if r["base_hit"] is False and r["loose_hit"] is True)
        a_loss = sum(1 for r in a_days if r["base_hit"] is True and r["loose_hit"] is False)
        a_both = sum(1 for r in a_days if r["base_hit"] is True and r["loose_hit"] is True)
        a_none = sum(1 for r in a_days if r["base_hit"] is False and r["loose_hit"] is False)
        lines.append(f"A能出串 {len(a_days)} 天: 基线中&Am中={a_both} 基线中&A未中={a_loss} 基线未中&A中={a_gain} 双未中={a_none}")
        lines.append(f"  → A 替换基线的净收益 = {a_gain - a_loss} 天")
        ab_hit = sum(1 for r in a_days if r["base_hit"] is True)
        aa_hit = sum(1 for r in a_days if r["loose_hit"] is True)
        lines.append(f"A天内: 基线命中={ab_hit}/{len(a_days)}={ab_hit/len(a_days):.3f}  A命中={aa_hit}/{len(a_days)}={aa_hit/len(a_days):.3f}")
        if a_drop:
            ad_hit = sum(1 for r in a_drop if r["base_hit"] is True)
            lines.append(f"A无组合的 {len(a_drop)} 天: 基线命中={ad_hit}/{len(a_drop)}={ad_hit/len(a_drop):.3f}")
        a_chain = 0
        for r in rows:
            if r["loose"] is not None and r["loose_hit"] is True:
                a_chain += 1
            elif r["loose"] is None and r["base_hit"] is True:
                a_chain += 1
        lines.append(f"降级链[A→fallback]命中={a_chain}/{len(rows)}")

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_parlay_c_vs_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out_path)


if __name__ == "__main__":
    asyncio.run(main())
