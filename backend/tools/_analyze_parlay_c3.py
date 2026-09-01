# -*- coding: utf-8 -*-
"""方案C 组合优选分析：当日全部合法组合中，"精选单串" vs "多串覆盖" 命中对比（只读）。

回答用户问题：
  Q1 多串提高的是单场命中还是串关命中？
  Q2 若多串有效，能否把多串场次精选成一个更合理的串关，同样提高命中？
核心：
  1) 命中组合在"组合p_hat降序"中的排名分布 → 判断 p_hat 是否能把命中组合排前面
  2) 多种选串判据（单串 top-1 命中率对比）
  3) 有解日 vs 无解日，命中组合特征 vs 未中组合特征
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
            had_pool = had_normal if had_normal else had
            combos = []
            for x in hafu:
                for y in had_pool:
                    if _same_match(x, y):
                        continue
                    combos.append({
                        "hafu": x, "had": y,
                        "odds": _pl_odds([x, y]), "p_hat": _pl_phat([x, y]),
                        "hit": _pl_hit([x, y]),
                        "in_range": MIN_ODDS <= _pl_odds([x, y]) <= MAX_ODDS,
                    })
            days.append({
                "matchday": matchday,
                "hafu": hafu, "had": had, "had_pool": had_pool,
                "combos": combos,
            })

        lines = []

        # ===== Q1: 多串提高的是"每日至少一中"（串关级） =====
        lines.append("===== Q1 多串提高的是串关级「每日至少一中」，不是单串命中 =====")
        # 单串命中率（每条组合的命中率）= 组合级 p_hit，多串不改变它
        all_c = [c for d in days for c in d["combos"] if c["hit"] is not None]
        lines.append(f"全部组合单串命中率: {sum(1 for c in all_c if c['hit'])}/{len(all_c)} = {sum(1 for c in all_c if c['hit'])/len(all_c):.3f}")
        lines.append("多串 = 当日选 N 个串，任一命中即日命中（概率覆盖），N 增加→每日至少一中上升，但每串命中率不变。")

        # ===== Q2a: 命中组合在"组合p_hat降序"中的排名分布 =====
        lines.append("\n===== Q2a 有解日：命中组合在「组合p_hat降序」里的排名分布 =====")
        rank_cnt = {}
        rank_days = []
        n_solvable = 0
        for d in days:
            settled = [c for c in d["combos"] if c["hit"] is not None]
            if not settled:
                continue
            sorted_c = sorted(settled, key=lambda x: -x["p_hat"])
            hit_combos = [c for c in sorted_c if c["hit"] is True]
            if not hit_combos:
                continue
            n_solvable += 1
            best_rank = min(sorted_c.index(c) + 1 for c in hit_combos)
            rank_cnt.setdefault(best_rank, 0)
            rank_cnt[best_rank] += 1
            rank_days.append((d["matchday"], best_rank, len(settled), hit_combos[0]))
        for r in sorted(rank_cnt):
            lines.append(f"  最优命中组合排名={r}: {rank_cnt[r]} 天")
        lines.append(f"  有解日共 {n_solvable} 天")
        # top-k 覆盖（有解日内）
        for k in (1, 2, 3, 5):
            covered = sum(v for r, v in rank_cnt.items() if r <= k)
            lines.append(f"  组合p_hat选top-{k} 覆盖命中组合: {covered}/{n_solvable} = {covered/n_solvable:.3f}（有解日内）")
        lines.append(f"  折合全部30天: top-1={sum(1 for _,r,_2,_3 in rank_days if r==1)}/30  top-3={sum(1 for _,r,_2,_3 in rank_days if r<=3)}/30")

        # ===== Q2b: 不同选串判据的 top-N 每日至少一中 =====
        lines.append("\n===== Q2b 选串判据对比（每日至少一中，全部30天） =====")

        def _crits(c):
            h, y = c["hafu"], c["had"]
            return {
                "组合p_hat": -c["p_hat"],
                "组合赔率": c["odds"],
                "半全场p_hat": -h["p_hat"],
                "胜平负p_hat": -y["p_hat"],
                "半全场赔率": h["odds"],
                "胜平负赔率": y["odds"],
            }

        names = list(_crits(days[0]["combos"][0]).keys())
        for name in names:
            seg = []
            for N in (1, 2, 3):
                ok = n_days = 0
                for d in days:
                    settled = [c for c in d["combos"] if c["hit"] is not None]
                    if not settled:
                        continue
                    n_days += 1
                    picked = []
                    used_h = set()
                    used_y = set()
                    for c in sorted(settled, key=lambda x: (_crits(x)[name], -x["p_hat"])):
                        if len(picked) >= N:
                            break
                        if c["hafu"]["match_num"] in used_h or c["had"]["match_num"] in used_y:
                            continue
                        picked.append(c)
                        used_h.add(c["hafu"]["match_num"])
                        used_y.add(c["had"]["match_num"])
                    if any(c["hit"] is True for c in picked):
                        ok += 1
                seg.append(f"{N}串:{ok}/{n_days}={ok/n_days:.3f}")
            lines.append(f"  {name}: " + " ".join(seg))

        # ===== Q2c: 有解日内，p_hat选top-1直接命中的天数 vs 落空天数 =====
        lines.append("\n===== Q2c 有解日 p_hat top-1 表现 =====")
        top1_hit = top1_miss = 0
        for d in days:
            settled = [c for c in d["combos"] if c["hit"] is not None]
            if not settled:
                continue
            top1 = sorted(settled, key=lambda x: -x["p_hat"])[0]
            if top1["hit"] is True:
                top1_hit += 1
            else:
                top1_miss += 1
        lines.append(f"  p_hat top-1: 命中 {top1_hit} 天 / 落空 {top1_miss} 天（全部30天口径：命中{top1_hit}/30）")

        # ===== Q2d: 命中组合 vs 未中组合特征（有解日全部组合） =====
        lines.append("\n===== Q2d 命中组合 vs 未中组合特征（有解日全部已结算组合） =====")
        hit_c = [c for c in all_c if c["hit"] is True]
        miss_c = [c for c in all_c if c["hit"] is False]
        lines.append(f"  命中组合数={len(hit_c)} 未中组合数={len(miss_c)}")

        def _avg(lst, key):
            if not lst:
                return 0.0
            return sum(x[key] for x in lst) / len(lst)

        for k, label in [("p_hat", "组合p_hat"), ("odds", "组合赔率")]:
            lines.append(f"  {label}: 命中组均值={_avg(hit_c, k):.3f} vs 未中组均值={_avg(miss_c, k):.3f}")
        lines.append(f"  半全场p_hat: 命中组={_avg([{'p_hat': c['hafu']['p_hat']} for c in hit_c], 'p_hat'):.3f}"
                     f" vs 未中组={_avg([{'p_hat': c['hafu']['p_hat']} for c in miss_c], 'p_hat'):.3f}")
        lines.append(f"  胜平负p_hat: 命中组={_avg([{'p_hat': c['had']['p_hat']} for c in hit_c], 'p_hat'):.3f}"
                     f" vs 未中组={_avg([{'p_hat': c['had']['p_hat']} for c in miss_c], 'p_hat'):.3f}")
        # ambiguous 占比
        amb_h = sum(1 for c in hit_c if c["had"]["source"] == "ambiguous_had") / len(hit_c)
        amb_m = sum(1 for c in miss_c if c["had"]["source"] == "ambiguous_had") / len(miss_c)
        lines.append(f"  胜平负ambiguous占比: 命中组={amb_h:.3f} vs 未中组={amb_m:.3f}")
        # 半全场选项 hh/aa 占比
        hh_ah = sum(1 for c in hit_c if c["hafu"]["hafu_key"] in ("hh", "aa")) / len(hit_c)
        hh_am = sum(1 for c in miss_c if c["hafu"]["hafu_key"] in ("hh", "aa")) / len(miss_c)
        lines.append(f"  半全场hh/aa占比: 命中组={hh_ah:.3f} vs 未中组={hh_am:.3f}")

        # ===== Q2e: 无解日（当日无命中组合） =====
        lines.append("\n===== Q2e 无解日列表（当日全部组合都不中） =====")
        n_unsolvable = 0
        for d in days:
            settled = [c for c in d["combos"] if c["hit"] is not None]
            if not settled:
                continue
            if not any(c["hit"] is True for c in settled):
                n_unsolvable += 1
                lines.append(f"  {d['matchday']}: 已结算组合 {len(settled)} 个, 半全场腿={len(d['hafu'])}, 胜平负腿={len(d['had_pool'])}")
        lines.append(f"  无解日共 {n_unsolvable} 天（占 {n_unsolvable}/{len(days)}）")

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_parlay_c_pick_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out_path)


if __name__ == "__main__":
    asyncio.run(main())
