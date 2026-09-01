# -*- coding: utf-8 -*-
"""方案A 8月逐场选场分析：重建每日候选池，对比实际选择 vs 未选候选的命中（只读）。

输出：
  1) 每个比赛日的方向腿 + 被选 2 条进球腿 + 全部进球腿候选（含未选），标注命中
  2) "被选未中但当日存在未选中且命中候选"的可优化场次
  3) 汇总统计
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import select

from app.api.market_flow import _market_flow_query, _matchday_date
from app.db.database import async_session
from app.db.models import TeamSeasonStats

DIR_ZH = {"home": "主", "draw": "平", "away": "客"}
MODE = os.getenv("MODE", "curr")  # curr=当前exp分层; all345=判大固定345; 345plus456=345优先456兜底


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
        print(f"items: {len(items)}")

        # ===== 球队攻守特性（与生产一致） =====
        from app.predictor.models.ou_direction import DEFAULT_TIER
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

        # ===== 重建每日候选（与生产 parlay 端点一致） =====
        by_day = {}
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

            # 进球腿候选
            ttg_p_big = (it.get("ou_all") or {}).get("standard", {}).get("p_big")
            ttg_dir = ("over" if (ttg_p_big is not None and ttg_p_big >= 0.62)
                       else "under" if (ttg_p_big is not None and ttg_p_big <= 0.38) else None)
            sm_dir = ((it.get("ou_sm") or {}).get("tiers") or {}).get("standard")
            ttg_valid = ttg_dir if ttg_dir in ("over", "under") else None
            sm_valid = sm_dir if sm_dir in ("over", "under") else None
            if ttg_valid and sm_valid and ttg_valid != sm_valid:
                dirn = None
            else:
                dirn = ttg_valid or sm_valid
            if dirn:
                ttg_odds = _parse_ttg(it.get("ttg_odds"))
                mkt = _implied(ttg_odds) if ttg_odds else None
                if mkt:
                    cand = [k for k in mkt if (k > 2.5 if dirn == "over" else k <= 2)]
                    # 方案A：判大选数（MODE 可切换）
                    if dirn == "over":
                        if MODE == "all345":
                            chosen = [3, 4, 5]  # 判大固定 345
                        elif MODE == "345plus456":
                            # 345 优先，仅当该场 345 缺数时才落 456
                            exp = _exp_total(it)
                            chosen = [4, 5, 6] if (exp is not None and exp >= 3.6) else [3, 4, 5]
                        else:
                            exp = _exp_total(it)
                            if exp is not None and exp >= 3.6:
                                chosen = [5, 6, 7]
                            elif exp is not None and exp >= 3.0:
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
                        d["goals"].append({
                            "match_num": it.get("match_num"),
                            "home_team": it.get("home_team"),
                            "away_team": it.get("away_team"),
                            "kickoff_time": it["kickoff_time"],
                            "pick": "/".join(str(x) for x in pick),
                            "dir": dirn,
                            "tier": tier,
                            "p_hat": round(sum(mkt[c] for c in pick), 4),
                            "odds": round(1.0 / sum(inv[c] for c in pick), 4),
                            "hit": (actual_tg in pick) if settled and actual_tg is not None else None,
                            "actual": str(actual_tg) if settled and actual_tg is not None else None,
                        })

            # 方向腿候选
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
                            "kind": "dir",
                            "source": "favorite_hafu",
                            "pick": "胜胜" if key == "hh" else "负负",
                            "p_hat": round(ip[key], 4),
                            "odds": round(odd, 4),
                            "hit": hit,
                            "actual": (f"半{it.get('half_home_score')}-{it.get('half_away_score')} 全{it.get('actual_score')}"
                                       if settled and hd is not None else None),
                        })
            elif pool == "ambiguous":
                fav = it.get("fav")
                fav_ip = it.get("fav_ip")
                if fav and fav in had and had.get(fav) and had[fav] >= 1.8 and isinstance(fav_ip, (int, float)):
                    d["dirs"].append({
                        "match_num": it.get("match_num"),
                        "home_team": it.get("home_team"),
                        "away_team": it.get("away_team"),
                        "kind": "dir",
                        "source": "ambiguous_had",
                        "pick": DIR_ZH[fav],
                        "p_hat": round(float(fav_ip), 4),
                        "odds": round(had[fav], 4),
                        "hit": (actual_outcome == fav) if settled else None,
                        "actual": DIR_ZH.get(actual_outcome, actual_outcome) if settled else None,
                    })

        # ===== 逐日组合（与生产一致）+ 输出 =====
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

        stats = {"A_curr": [], "B_phat": [], "C_small": []}
        daily_multi = []
        out = []
        all_sel_g, all_unsel_g, all_dir = [], [], []
        for matchday, grp in sorted(by_day.items()):
            gs = sorted(grp["goals"], key=lambda x: (x.get("tier", 0), -x["p_hat"]))
            ds = sorted(grp["dirs"], key=lambda x: x["p_hat"], reverse=True)
            if len(gs) < 2 or not ds:
                continue
            d0 = ds[0]
            avail = [g for g in gs if not _same_match(g, d0)]
            if len(avail) < 2:
                continue
            # 策略A：当前生产（tier 优先）
            legs_a = avail[:2] + [d0]
            # 策略B：纯 p_hat 降序
            gs_b = sorted(grp["goals"], key=lambda x: -x["p_hat"])
            avail_b = [g for g in gs_b if not _same_match(g, d0)]
            legs_b = (avail_b[:2] + [d0]) if len(avail_b) >= 2 else legs_a
            # 策略C：判小优先（判小 > tier > p_hat）
            gs_c = sorted(grp["goals"], key=lambda x: (0 if x["dir"] == "under" else 1, x.get("tier", 0), -x["p_hat"]))
            avail_c = [g for g in gs_c if not _same_match(g, d0)]
            legs_c = (avail_c[:2] + [d0]) if len(avail_c) >= 2 else legs_a
            stats["A_curr"].append((_pl_hit(legs_a), _pl_odds(legs_a)))
            stats["B_phat"].append((_pl_hit(legs_b), _pl_odds(legs_b)))
            stats["C_small"].append((_pl_hit(legs_c), _pl_odds(legs_c)))
            # 每日多串模拟：每串用不同方向腿（d0/d1/d2），进球腿分段不重复
            day_pls = []
            used = set()
            for dd in ds[:3]:
                cand = [g for g in gs if g["match_num"] not in used and not _same_match(g, dd)]
                if len(cand) < 2:
                    continue
                two = cand[:2]
                used.update(g["match_num"] for g in two)
                day_pls.append(_pl_hit(two + [dd]))
                if len(day_pls) >= 3:
                    break
            daily_multi.append(day_pls)
            all_sel_g += avail[:2]
            all_unsel_g += [g for g in gs if g not in avail[:2]]
            all_dir.append(d0)
            row = {
                "matchday": matchday,
                "dir": d0,
                "goals_sel": avail[:2],
                "goals_all": gs,
                "hit": _pl_hit(legs_a),
            }
            out.append(row)

        # ===== 输出 =====
        total = 0
        fail = 0
        improvable = 0
        lines = []
        for row in out:
            total += 1
            md = row["matchday"]
            d0 = row["dir"]
            sel = row["goals_sel"]
            allg = row["goals_all"]
            row_hit = row["hit"]
            if row_hit is False:
                fail += 1
            unsel = [g for g in allg if g not in sel]
            unsel_hit = [g for g in unsel if g["hit"] is True]
            flag = " [可优化]" if (row_hit is False and unsel_hit) else ""
            if row_hit is False and unsel_hit:
                improvable += 1
            lines.append(f"[{md}] 串关{'命中' if row_hit else '未中' if row_hit is False else '未结算'}"
                         f" | 方向腿: {d0['home_team']}vs{d0['away_team']} {d0['pick']}@{d0['odds']} p_hat={d0['p_hat']}"
                         f" {'命中' if d0['hit'] else '未中' if d0['hit'] is False else '-'}{flag}")
            for g in sel:
                lines.append(f"  选中进球: {g['match_num']} {g['home_team']}vs{g['away_team']} {g['pick']}"
                             f" p_hat={g['p_hat']} tier={g['tier']} {'命中' if g['hit'] else '未中' if g['hit'] is False else '-'}")
            for g in unsel:
                lines.append(f"  候选未选: {g['match_num']} {g['home_team']}vs{g['away_team']} {g['pick']}"
                             f" p_hat={g['p_hat']} tier={g['tier']} {'命中' if g['hit'] else '未中' if g['hit'] is False else '-'}")
        lines.append(f"\n===== 汇总: 串关={total} 未中={fail} 其中'当日存在未选且命中候选'={improvable} =====")

        # 策略对比
        def _summarize(k):
            rows = [x for x in stats[k] if x[0] is not None]
            n = len(rows)
            hit = sum(1 for h, _ in rows if h)
            ph = hit / n if n else 0
            av = sum(o for _, o in rows) / n if n else 0
            return n, hit, ph, av, av * ph - 1

        lines.append("\n===== 选场策略对比（8月，已结算串关） =====")
        for k, name in [("A_curr", "A:当前tier优先"), ("B_phat", "B:纯p_hat降序"), ("C_small", "C:判小优先")]:
            n, hit, ph, av, roi = _summarize(k)
            lines.append(f"{name}: n={n} 命中={hit} p_hit={ph:.4f} 均赔={av:.2f} ROI={roi:+.4f}")

        # 每日多串：每日至少一中率
        lines.append("\n===== 每日多串（最多3串，方向腿固定d0）====")
        base = [p[0] for p in daily_multi if p]
        if base:
            hb = sum(1 for h in base if h is True)
            lines.append(f"基准(每日1串): 每日至少一中={hb}/{len(base)} = {hb/len(base):.3f}")
        for k in (2, 3):
            cand_days = [p for p in daily_multi if len(p) >= k]
            if not cand_days:
                continue
            hit_days = sum(1 for p in cand_days if any(h is True for h in p[:k]))
            lines.append(f"每日{k}串: 可出串={len(cand_days)}天 每日至少一中={hit_days} = {hit_days/len(cand_days):.3f}")

        # 被选 vs 未选候选命中率（进球腿，按选数分组）
        def _pick_hit_rate(lst):
            settled = [g for g in lst if g["hit"] is not None]
            if not settled:
                return {}
            out2 = {}
            by_pick = {}
            for g in settled:
                by_pick.setdefault(g["pick"], []).append(g)
            for pk, grp in sorted(by_pick.items()):
                h = sum(1 for g in grp if g["hit"])
                out2[pk] = (len(grp), h, h / len(grp))
            return out2

        lines.append("\n===== 进球腿 被选 vs 未选候选（按选数） =====")
        lines.append("--- 被选 ---")
        for pk, (n2, h, r) in sorted(_pick_hit_rate(all_sel_g).items()):
            lines.append(f"  {pk}: n={n2} 命中={h} p={r:.3f}")
        lines.append("--- 未选候选 ---")
        for pk, (n2, h, r) in sorted(_pick_hit_rate(all_unsel_g).items()):
            lines.append(f"  {pk}: n={n2} 命中={h} p={r:.3f}")
        dg = [d for d in all_dir if d["hit"] is not None]
        dh = sum(1 for d in dg if d["hit"])
        lines.append(f"\n方向腿: n={len(dg)} 命中={dh} p={dh/len(dg):.3f}")
        bysrc = {}
        for d in dg:
            bysrc.setdefault(d["source"], []).append(d)
        for src, grp in sorted(bysrc.items()):
            h = sum(1 for d in grp if d["hit"])
            lines.append(f"  {src}: n={len(grp)} 命中={h} p={h/len(grp):.3f}")
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_parlay_a_report_{MODE}_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"报告已写入 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
