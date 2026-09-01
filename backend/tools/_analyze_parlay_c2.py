# -*- coding: utf-8 -*-
"""方案C 深化分析：已选腿命中 / 失败日拆解 / 每日多串 top-N / 腿门槛收紧策略对比（只读）。"""
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

        # ===== 重建候选（同生产 parlay-dir） =====
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

        # ===== 每日组合池（全部合法组合） + 生产选择 =====
        days = []
        for matchday, grp in sorted(by_day.items()):
            hafu = sorted(grp["hafu"], key=lambda x: x["p_hat"], reverse=True)
            had = sorted(grp["had"], key=lambda x: x["p_hat"], reverse=True)
            if not hafu or not had:
                continue
            had_normal = [x for x in had if not x.get("fallback")]
            had_pool = had_normal if had_normal else had
            # 生产选择
            legs = None
            for a in hafu:
                b = next((x for x in had_pool if not _same_match(a, x)), None)
                if b is not None:
                    legs = [a, b]
                    break
            # 全部合法组合
            combos = []
            for x in hafu:
                for y in had_pool:
                    if _same_match(x, y):
                        continue
                    combos.append({"hafu": x, "had": y})
            for c in combos:
                c["odds"] = _pl_odds([c["hafu"], c["had"]])
                c["p_hat"] = _pl_phat([c["hafu"], c["had"]])
                c["hit"] = _pl_hit([c["hafu"], c["had"]])
                c["in_range"] = MIN_ODDS <= c["odds"] <= MAX_ODDS
            days.append({
                "matchday": matchday,
                "hafu": hafu, "had": had, "had_pool": had_pool,
                "prod_legs": legs,
                "prod_hit": _pl_hit(legs) if legs else None,
                "combos": combos,
            })

        lines = []
        # ===== 1) 已选腿命中率 =====
        sel_hafu = [d["prod_legs"][0] for d in days if d["prod_legs"]]
        sel_had = [d["prod_legs"][1] for d in days if d["prod_legs"]]

        def _rate(lst):
            s = [x for x in lst if x["hit"] is not None]
            if not s:
                return 0, 0, 0.0
            h = sum(1 for x in s if x["hit"])
            return len(s), h, h / len(s)

        lines.append("===== 1) 已选腿命中率（生产每日1串） =====")
        n1, h1, p1 = _rate(sel_hafu)
        n2, h2, p2 = _rate(sel_had)
        lines.append(f"已选半全场腿: n={n1} 命中={h1} p={p1:.3f}")
        lines.append(f"已选胜平负腿: n={n2} 命中={h2} p={p2:.3f}")

        # ===== 2) 失败日拆解 =====
        lines.append("\n===== 2) 未中串关的失败腿拆解（谁错了） =====")
        stats = {"both": 0, "hafu_only": 0, "had_only": 0, "both_hit": 0, "unsettled": 0}
        for d in days:
            if d["prod_legs"] is None:
                continue
            hh_ = d["prod_legs"][0]["hit"]
            hd_ = d["prod_legs"][1]["hit"]
            if hh_ is None or hd_ is None:
                stats["unsettled"] += 1
                continue
            if hh_ and hd_:
                stats["both_hit"] += 1
            elif hh_ is False and hd_ is False:
                stats["both"] += 1
            elif hh_ is False:
                stats["hafu_only"] += 1
            else:
                stats["had_only"] += 1
        for k, v in stats.items():
            lines.append(f"  {k}: {v} 天")
        # 仅半全场错 / 仅胜平负错 的失败日里，当日是否可用"仅 ambiguous 胜平负"或换半全场救回
        lines.append("--- 失败日细分（半全场错/胜平负错的当天替代方案） ---")
        fix_hafu = fix_had = fix_both = 0
        for d in days:
            if d["prod_legs"] is None or d["prod_hit"] is not False:
                continue
            hh_ = d["prod_legs"][0]["hit"]
            hd_ = d["prod_legs"][1]["hit"]
            # 当日替代组合（与生产同口径：had_pool 全部）
            alt_hit = any(c["hit"] is True for c in d["combos"])
            # 当日仅用 ambiguous 胜平负腿（剔除兜底/冷门）的替代
            amb = [x for x in d["had"] if x["source"] == "ambiguous_had"]
            combos_amb = []
            if amb:
                combos_amb = [c for c in d["combos"] if c["had"] in amb]
            amb_hit = any(c["hit"] is True for c in combos_amb)
            if hh_ is False and hd_ is False:
                fix_both += 1 if (alt_hit or amb_hit) else 0
            elif hh_ is False:
                fix_hafu += 1 if alt_hit else 0
            else:
                fix_had += 1 if alt_hit else 0
        lines.append(f"  双错日且当日有命中替代: {fix_both} 天")
        lines.append(f"  仅半全场错日且当日有命中替代: {fix_hafu} 天")
        lines.append(f"  仅胜平负错日且当日有命中替代: {fix_had} 天")

        # ===== 3) 每日多串 top-N（组合按 p_hat 降序，贪心腿不重复） =====
        lines.append("\n===== 3) 每日多串 top-N（腿不重复，按组合 p_hat 降序） =====")
        for mode, label in [("all", "全部组合"), ("inrange", "仅赔率[4,10]")]:
            lines.append(f"--- {label} ---")
            for N in (1, 2, 3, 4):
                ok = n_days = 0
                for d in days:
                    combos = [c for c in d["combos"] if c["hit"] is not None]
                    if mode == "inrange":
                        combos = [c for c in combos if c["in_range"]]
                    if not combos:
                        continue
                    n_days += 1
                    picked = []
                    used_h = set()
                    used_y = set()
                    for c in sorted(combos, key=lambda x: -x["p_hat"]):
                        if len(picked) >= N:
                            break
                        if c["hafu"]["match_num"] in used_h or c["had"]["match_num"] in used_y:
                            continue
                        picked.append(c)
                        used_h.add(c["hafu"]["match_num"])
                        used_y.add(c["had"]["match_num"])
                    if any(c["hit"] is True for c in picked):
                        ok += 1
                lines.append(f"  每日{N}串: 至少一中 {ok}/{n_days} = {ok/n_days:.3f}" if n_days else f"  每日{N}串: 无数据")

        # ===== 4) 腿门槛收紧策略对比 =====
        lines.append("\n===== 4) 腿门槛收紧策略对比（每日多串 1/2/3） =====")
        strategies = [
            ("当前生产", lambda h: True, lambda y: True),
            ("半全场p_hat>=0.35", lambda h: h["p_hat"] >= 0.35, lambda y: True),
            ("半全场赔率<=3.0", lambda h: h["odds"] <= 3.0, lambda y: True),
            ("半全场仅hh/aa", lambda h: h["hafu_key"] in ("hh", "aa"), lambda y: True),
            ("胜平负仅ambiguous", lambda h: True, lambda y: y["source"] == "ambiguous_had"),
            ("胜平负仅ambiguous且赔率<2.5", lambda h: True, lambda y: y["source"] == "ambiguous_had" and y["odds"] < 2.5),
            ("双收紧(p35+ambiguous)", lambda h: h["p_hat"] >= 0.35, lambda y: y["source"] == "ambiguous_had"),
        ]
        for name, fh, fy in strategies:
            line = f"  {name}: "
            seg = []
            for N in (1, 2, 3):
                ok = n_days = tot_hit = 0
                tot_odds = 0.0
                for d in days:
                    hafu_f = [x for x in d["hafu"] if fh(x)]
                    had_f = [x for x in d["had_pool"] if fy(x)]
                    combos = [{"hafu": a, "had": b, "odds": _pl_odds([a, b]), "p_hat": _pl_phat([a, b]), "hit": _pl_hit([a, b])}
                              for a in hafu_f for b in had_f if not _same_match(a, b)]
                    combos = [c for c in combos if c["hit"] is not None]
                    if not combos:
                        continue
                    n_days += 1
                    picked = []
                    used_h = set()
                    used_y = set()
                    for c in sorted(combos, key=lambda x: -x["p_hat"]):
                        if len(picked) >= N:
                            break
                        if c["hafu"]["match_num"] in used_h or c["had"]["match_num"] in used_y:
                            continue
                        picked.append(c)
                        used_h.add(c["hafu"]["match_num"])
                        used_y.add(c["had"]["match_num"])
                    if any(c["hit"] is True for c in picked):
                        ok += 1
                seg.append(f"{N}串:{ok}/{n_days}={ok/n_days:.3f}" if n_days else f"{N}串:无")
            lines.append(line + " ".join(seg))

        # ===== 5) 每日可出串数分布 =====
        lines.append("\n===== 5) 每日合法组合数分布（全部 vs 赔率[4,10]内） =====")
        dist_all = {}
        dist_inr = {}
        for d in days:
            c_all = [c for c in d["combos"] if c["hit"] is not None]
            c_inr = [c for c in c_all if c["in_range"]]
            dist_all.setdefault(len(c_all), 0)
            dist_all[len(c_all)] += 1
            dist_inr.setdefault(len(c_inr), 0)
            dist_inr[len(c_inr)] += 1
        lines.append("  全部组合: " + ", ".join(f"{k}天有{k2}串" for k2 in sorted(dist_all) for k in [dist_all[k2]]))
        lines.append("  [4,10]内: " + ", ".join(f"{k}天有{k2}串" for k2 in sorted(dist_inr) for k in [dist_inr[k2]]))

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_parlay_c_deep_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out_path)


if __name__ == "__main__":
    asyncio.run(main())
