# -*- coding: utf-8 -*-
"""方案B（仅大球3串1）命中率过低根因分析：逐日拆解进球腿/方向腿命中、判大进球分布、选数档位（只读）。

输出：
  1) 逐日：已选串的进球腿命中、方向腿命中、失败原因拆解
  2) 汇总：进球腿命中率 vs 方向腿命中率 vs 组合命中率
  3) 判大场次实际总进球分布（对照选数档位 345/456/567）
  4) 选数档位（345/456/567）各自命中率
  5) 对照：方案A（含判小）进球腿命中 vs 方案B（仅判大）进球腿命中
"""
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import select

from app.api.market_flow import _market_flow_query, _matchday_date
from app.db.database import async_session
from app.db.models import TeamSeasonStats

DIR_ZH = {"home": "主", "draw": "平", "away": "客"}


def _parse_ttg(raw):
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
    out = {}
    for k, v in data.items():
        try:
            o = float(v)
        except (TypeError, ValueError):
            continue
        if o > 0:
            out[int(k)] = o
    return out


def _implied(odds_map):
    inv = {k: 1.0 / v for k, v in odds_map.items() if v and v > 0}
    if not inv:
        return None
    tot = sum(inv.values())
    return {k: v / tot for k, v in inv.items()}


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


def _half_dir(it):
    hh, ha = it.get("half_home_score"), it.get("half_away_score")
    if not isinstance(hh, int) or not isinstance(ha, int):
        return None
    return "home" if hh > ha else "away" if hh < ha else "draw"


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

        _team_avg = {}
        _tids = set()
        for it in items:
            if it.get("home_team_id"):
                _tids.add(int(it["home_team_id"]))
            if it.get("away_team_id"):
                _tids.add(int(it["away_team_id"]))
        if _tids:
            _tss = (await db.execute(
                select(TeamSeasonStats.team_id, TeamSeasonStats.goals_for,
                       TeamSeasonStats.goals_against, TeamSeasonStats.played)
                .where(TeamSeasonStats.team_id.in_(_tids), TeamSeasonStats.played > 0)
            )).all()
            _best = {}
            for _tid, _gf, _ga, _played in _tss:
                _tid = int(_tid)
                if _tid not in _best or _played > _best[_tid][2]:
                    _best[_tid] = (_gf, _ga, _played)
            for _tid, (_gf, _ga, _played) in _best.items():
                if _played and _played > 0:
                    _team_avg[_tid] = (_gf / _played, _ga / _played)

        def _home_attack_strong(it):
            tid = it.get("home_team_id")
            if tid is None:
                return None
            avg = _team_avg.get(int(tid))
            return (avg[0] - avg[1] >= 0) if avg else None

        def _exp_total(it):
            h = _team_avg.get(int(it["home_team_id"])) if it.get("home_team_id") else None
            a = _team_avg.get(int(it["away_team_id"])) if it.get("away_team_id") else None
            if not h or not a:
                return None
            return (h[0] + a[1]) / 2 + (a[0] + h[1]) / 2

        # ===== 重建每日候选（与生产 parlay 端点一致：strict优先退standard + gate排序 + 三级降级） =====
        by_day = {}
        all_over_legs = []   # 全部判大进球腿（含未选），用于进球分布分析
        all_a_legs = []      # 方案A全部进球腿（含判小），对照
        for it in items:
            d = by_day.setdefault(
                _matchday_date(it["kickoff_time"], match_num=it.get("match_num")),
                {"goals": [], "dirs": []},
            )
            settled = it.get("actual_outcome") is not None
            actual_tg = it.get("actual_total_goals")
            actual_outcome = it.get("actual_outcome")
            had = it.get("had_odds") or {}
            pool = it.get("pool")

            _ttg_all = it.get("ou_all") or {}
            ttg_strict = (_ttg_all.get("strict") or {}).get("direction")
            ttg_dir = ttg_strict if ttg_strict in ("over", "under") else (_ttg_all.get("standard") or {}).get("direction")
            _sm_tiers = ((it.get("ou_sm") or {}).get("tiers") or {})
            sm_dir = _sm_tiers.get("strict")
            if sm_dir not in ("over", "under"):
                sm_dir = _sm_tiers.get("standard")
            ttg_valid = ttg_dir if ttg_dir in ("over", "under") else None
            sm_valid = sm_dir if sm_dir in ("over", "under") else None
            if ttg_valid and sm_valid and ttg_valid != sm_valid:
                dirn = None
            else:
                dirn = ttg_valid or sm_valid
            gate = "standard"
            if dirn and (ttg_strict == dirn or _sm_tiers.get("strict") == dirn):
                gate = "strict"

            if dirn:
                ttg_odds = _parse_ttg(it.get("ttg_odds"))
                mkt = _implied(ttg_odds) if ttg_odds else None
                if mkt:
                    cand = [k for k in mkt if (k > 2.5 if dirn == "over" else k <= 2)]
                    if dirn == "over":
                        # 方案B：盘口 3.5 线分层（与生产一致）
                        _p35 = None
                        _l35 = ((it.get("ou_sm") or {}).get("lines") or {}).get("3.5")
                        if _l35:
                            _p35 = _l35.get("p_big")
                        if _p35 is None:
                            exp = _exp_total(it)
                            if exp is not None and exp >= 3.6:
                                chosen = [5, 6, 7]
                            elif exp is not None and exp >= 3.0:
                                chosen = [4, 5, 6]
                            else:
                                chosen = [3, 4, 5]
                        elif _p35 >= 0.45:
                            chosen = [4, 5, 6]
                        else:
                            chosen = [3, 4, 5]
                        pick = [c for c in chosen if c in mkt]
                        if len(pick) < 3:
                            extra = [c for c in cand if c not in pick]
                            extra.sort(key=lambda c: mkt[c], reverse=True)
                            pick = (pick + extra)[:3]
                    else:
                        pick = sorted(cand, key=mkt.get, reverse=True)[:3]
                    if len(pick) >= 3:
                        inv = {k: 1.0 / v for k, v in ttg_odds.items() if v > 0}
                        tier = 0
                        if dirn == "over":
                            home_fav = it.get("fav") == "home"
                            attack = _home_attack_strong(it)
                            if home_fav and attack is True:
                                tier = 0
                            elif attack is True:
                                tier = 1
                            elif home_fav:
                                tier = 2
                            else:
                                tier = 3
                        leg = {
                            "match_num": it.get("match_num"),
                            "home_team": it.get("home_team"),
                            "away_team": it.get("away_team"),
                            "league_name": it.get("league_name"),
                            "kickoff_time": it["kickoff_time"],
                            "pick": "/".join(str(x) for x in pick),
                            "dir": dirn,
                            "gate": gate,
                            "tier": tier,
                            "p_hat": round(sum(mkt[c] for c in pick), 4),
                            "odds": round(1.0 / sum(inv[c] for c in pick), 4),
                            "hit": (actual_tg in pick) if settled and actual_tg is not None else None,
                            "actual": str(actual_tg) if settled and actual_tg is not None else None,
                            "p_big": (mkt.get(3, 0) + mkt.get(4, 0) + mkt.get(5, 0) + mkt.get(6, 0) + mkt.get(7, 0) + mkt.get(8, 0)) if dirn == "over" else None,
                        }
                        d["goals"].append(leg)
                        all_a_legs.append(leg)
                        if dirn == "over":
                            all_over_legs.append(leg)

            if pool == "favorite":
                fav = it.get("fav")
                hafu = _parse_hafu(it.get("hafu_odds"))
                if fav in ("home", "away") and hafu:
                    key = "hh" if fav == "home" else "aa"
                    odd = hafu.get(key)
                    ip = _implied(hafu) if hafu else None
                    if odd and odd >= 1.8 and ip and ip.get(key):
                        hd = _half_dir(it)
                        hit = None
                        if settled:
                            hit = (hd == "home" and actual_outcome == "home") if fav == "home" else (hd == "away" and actual_outcome == "away")
                            if hd is None:
                                hit = None
                        d["dirs"].append({
                            "match_num": it.get("match_num"),
                            "home_team": it.get("home_team"),
                            "away_team": it.get("away_team"),
                            "league_name": it.get("league_name"),
                            "kickoff_time": it["kickoff_time"],
                            "kind": "dir",
                            "source": "favorite_hafu",
                            "pick": "胜胜" if key == "hh" else "负负",
                            "p_hat": round(ip[key], 4),
                            "odds": round(odd, 4),
                            "hit": hit,
                        })
            elif pool == "ambiguous":
                fav = it.get("fav")
                fav_ip = it.get("fav_ip")
                if fav and fav in had and had.get(fav) and had[fav] >= 1.8 and isinstance(fav_ip, (int, float)):
                    d["dirs"].append({
                        "match_num": it.get("match_num"),
                        "home_team": it.get("home_team"),
                        "away_team": it.get("away_team"),
                        "league_name": it.get("league_name"),
                        "kickoff_time": it["kickoff_time"],
                        "kind": "dir",
                        "source": "ambiguous_had",
                        "pick": DIR_ZH[fav],
                        "p_hat": round(float(fav_ip), 4),
                        "odds": round(had[fav], 4),
                        "hit": (actual_outcome == fav) if settled else None,
                    })

        def _same_match(a, b):
            if a.get("match_num") and b.get("match_num"):
                return a["match_num"] == b["match_num"]
            return a.get("kickoff_time") == b.get("kickoff_time")

        def _pl_odds(legs):
            o = 1.0
            for l in legs:
                o *= l["odds"]
            return o

        def _pl_hit(legs):
            if not all(l["hit"] is not None for l in legs):
                return None
            return all(l["hit"] for l in legs)

        def _build_over(grp):
            """方案B：仅判大进球腿 + gate排序 + 三级降级"""
            gs = [g for g in grp["goals"] if g["dir"] == "over"]
            gs = sorted(gs, key=lambda x: (
                0 if x.get("gate") == "strict" else 1,
                x.get("tier", 0), -x["p_hat"],
            ))
            ds = sorted(grp["dirs"], key=lambda x: x["p_hat"], reverse=True)
            if len(gs) < 2 or not ds:
                return None, None
            d0 = ds[0]
            def _not_same(g, dd):
                return not (
                    (g["match_num"] and dd.get("match_num") and g["match_num"] == dd.get("match_num"))
                    or (not (g["match_num"] and dd.get("match_num")) and g["kickoff_time"] == dd.get("kickoff_time"))
                )
            avail = [g for g in gs if _not_same(g, d0)]
            if len(avail) < 2:
                return None, None
            def _find_pair(avail_):
                for i in range(len(avail_)):
                    for j in range(i + 1, len(avail_)):
                        if avail_[i].get("league_name") != avail_[j].get("league_name"):
                            return (avail_[i], avail_[j])
                return None
            chosen = None
            level = "strict"
            for dd in ds:
                aa_s = [g for g in gs if _not_same(g, dd) and g.get("league_name") != dd.get("league_name")]
                pair = _find_pair(aa_s)
                if pair:
                    chosen = (pair[0], pair[1], dd)
                    break
            if chosen is None:
                pair = _find_pair(avail)
                if pair:
                    level = "loose"
                    chosen = (pair[0], pair[1], d0)
                else:
                    level = "fallback"
                    chosen = (avail[0], avail[1], d0)
            return list(chosen), level

        lines = []
        # ===== 1) 逐日输出 + 失败拆解 =====
        rows = []
        lines.append(f"===== 方案B 8月逐日拆解（{_win}） =====")
        for matchday, grp in sorted(by_day.items()):
            legs, level = _build_over(grp)
            if legs is None:
                continue
            g1, g2, dd = legs
            ph = _pl_hit(legs)
            rows.append({"matchday": matchday, "legs": legs, "hit": ph, "level": level})
            g1s = "中" if g1["hit"] else "未中" if g1["hit"] is False else "-"
            g2s = "中" if g2["hit"] else "未中" if g2["hit"] is False else "-"
            dds = "中" if dd["hit"] else "未中" if dd["hit"] is False else "-"
            lines.append(f"[{matchday}] 串={'命中' if ph else '未中' if ph is False else '未结算'} combo={level}")
            lines.append(f"  进1: {g1['home_team']}vs{g1['away_team']} {g1['pick']} p_big={g1['p_big']} 实际={g1['actual']} {g1s}")
            lines.append(f"  进2: {g2['home_team']}vs{g2['away_team']} {g2['pick']} p_big={g2['p_big']} 实际={g2['actual']} {g2s}")
            lines.append(f"  方向: {dd['home_team']}vs{dd['away_team']} {dd['pick']}@{dd['odds']} {dds}")

        # ===== 2) 汇总：腿命中 =====
        settled_rows = [r for r in rows if r["hit"] is not None]
        sel_g = [lg for r in settled_rows for lg in r["legs"][:2]]
        sel_d = [r["legs"][2] for r in settled_rows]
        g_set = [x for x in sel_g if x["hit"] is not None]
        d_set = [x for x in sel_d if x["hit"] is not None]
        gh = sum(1 for x in g_set if x["hit"])
        dh = sum(1 for x in d_set if x["hit"])
        lines.append(f"\n===== 汇总 =====")
        lines.append(f"串关: n={len(settled_rows)} 命中={sum(1 for r in settled_rows if r['hit'])} p_hit={sum(1 for r in settled_rows if r['hit'])/len(settled_rows):.3f}")
        lines.append(f"进球腿(判大3选): n={len(g_set)} 命中={gh} p={gh/len(g_set):.3f}")
        lines.append(f"方向腿: n={len(d_set)} 命中={dh} p={dh/len(d_set):.3f}")
        # 失败拆解
        both = g_only = d_only = 0
        for r in settled_rows:
            g1, g2, dd = r["legs"]
            gh1 = g1["hit"] and g2["hit"]
            if r["hit"] is False:
                if gh1 is False and dd["hit"] is False:
                    both += 1
                elif gh1 is False:
                    g_only += 1
                else:
                    d_only += 1
        lines.append(f"失败日拆解: 双错={both} 仅进球腿错={g_only} 仅方向腿错={d_only}")

        # ===== 3) 判大进球腿实际进球分布（全部候选） =====
        lines.append(f"\n===== 判大进球腿实际总进球分布（全部候选 n={len(all_over_legs)}） =====")
        tg_dist = {}
        for x in all_over_legs:
            if x["actual"] is not None:
                k = int(x["actual"])
                tg_dist[k] = tg_dist.get(k, 0) + 1
        for k in sorted(tg_dist):
            lines.append(f"  实际{k}球: {tg_dist[k]}")

        # ===== 4) 选数档位命中率（判大） =====
        lines.append(f"\n===== 判大选数档位命中率（全部候选） =====")
        by_pick = {}
        for x in all_over_legs:
            by_pick.setdefault(x["pick"], []).append(x)
        for pk in sorted(by_pick):
            grp = [x for x in by_pick[pk] if x["hit"] is not None]
            if not grp:
                continue
            h = sum(1 for x in grp if x["hit"])
            lines.append(f"  {pk}: n={len(grp)} 命中={h} p={h/len(grp):.3f}")

        # ===== 5) 方案A（含判小）进球腿命中对照 =====
        a_set = [x for x in all_a_legs if x["hit"] is not None]
        ah = sum(1 for x in a_set if x["hit"])
        lines.append(f"\n===== 对照 =====")
        lines.append(f"方案A进球腿(大小球均可): n={len(a_set)} 命中={ah} p={ah/len(a_set):.3f}")
        over_set = [x for x in all_over_legs if x["hit"] is not None]
        oh = sum(1 for x in over_set if x["hit"])
        lines.append(f"方案B进球腿(仅判大): n={len(over_set)} 命中={oh} p={oh/len(over_set):.3f}")
        under_set = [x for x in all_a_legs if x["dir"] == "under" and x["hit"] is not None]
        if under_set:
            uh = sum(1 for x in under_set if x["hit"])
            lines.append(f"判小进球腿: n={len(under_set)} 命中={uh} p={uh/len(under_set):.3f}")
        # p_hat 区间 vs 命中（判大）
        lines.append(f"\n判大进球腿 按 p_hat 区间:")
        for lo, hi in [(0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.01)]:
            grp = [x for x in over_set if lo <= x["p_hat"] < hi]
            if grp:
                h = sum(1 for x in grp if x["hit"])
                lines.append(f"  p_hat {lo:.2f}~{hi:.2f}: n={len(grp)} 命中={h} p={h/len(grp):.3f}")
        # 判大方向命中（对照）
        lines.append(f"\n判大方向本身命中率（对照，TTG判大场次实际>2.5球）:")
        od_set = [x for x in all_over_legs if x["actual"] is not None]
        od_h = sum(1 for x in od_set if int(x["actual"]) > 2.5)
        lines.append(f"  判大场次方向命中(实际≥3球): {od_h}/{len(od_set)} = {od_h/len(od_set):.3f}")

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_plan_b_fail_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out_path)


if __name__ == "__main__":
    asyncio.run(main())
