"""方向1 信号参数稳健性：gap 阈值 / 主客拆分 / points 口径 / 平局成分

确认最优冲突定义，并检验信号是否依赖单一模式。
口径与 _cold_factor_v2.py 一致。
"""
import asyncio, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot, TeamSeasonStats
from scipy.stats import fisher_exact

OUT = []
def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

W_START = datetime(2026, 7, 16, 12, 0)
W_END = datetime(2026, 8, 15, 12, 0)

def implied_from_odds(h, d, a):
    if not h or not d or not a or min(h, d, a) <= 1.01:
        return None
    ih, id_, ia = 1/h, 1/d, 1/a
    tot = ih + id_ + ia
    return ih/tot, id_/tot, ia/tot

async def main():
    async with async_session() as db:
        rows = (await db.execute(
            select(Prediction, Match).join(Match, Prediction.match_id == Match.id)
            .where(Prediction.result_spf != 0)
            .where(Match.kickoff_time >= W_START, Match.kickoff_time < W_END)
        )).all()
        match_ids = [p.match_id for p, _ in rows]
        kickoff = {p.match_id: m.kickoff_time for p, m in rows}
        teams = {p.match_id: (m.home_team_id, m.away_team_id) for p, m in rows}

        snaps = (await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id.in_(match_ids))
        )).scalars().all()
        by_match = defaultdict(list)
        for s in snaps:
            kt = kickoff.get(s.match_id)
            if kt and s.snapshot_time < kt:
                by_match[s.match_id].append(s)

        all_tids = set()
        for h, a in teams.values():
            if h: all_tids.add(h)
            if a: all_tids.add(a)
        stat_rows = (await db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.team_id.in_(all_tids))
        )).scalars().all()
        rank_by_team = {}
        for st in stat_rows:
            if st.league_position is not None:
                key = st.team_id
                if key not in rank_by_team or (st.league_id and not rank_by_team[key].get("league_id")):
                    rank_by_team[key] = {"position": st.league_position, "points": st.league_points, "league_id": st.league_id}

        samples = []
        for pred, m in rows:
            if pred.actual_home_score is None or pred.actual_away_score is None or pred.match_id < 15000:
                continue
            mid = pred.match_id
            kt = kickoff[mid]
            actual = 0 if pred.actual_home_score > pred.actual_away_score else (
                1 if pred.actual_home_score == pred.actual_away_score else 2)
            snap_list = by_match.get(mid)
            if not snap_list: continue
            by_time = defaultdict(list)
            for s in snap_list:
                by_time[s.snapshot_time].append(s)
            times = sorted(by_time.keys())
            if len(times) < 2: continue
            def consensus(t):
                spf = {}
                for s in by_time[t]:
                    bm = s.bookmaker or "unknown"
                    if bm not in spf and s.home_win and s.draw and s.away_win:
                        spf[bm] = s
                if not spf: return None
                return implied_from_odds(
                    np.mean([s.home_win for s in spf.values()]),
                    np.mean([s.draw for s in spf.values()]),
                    np.mean([s.away_win for s in spf.values()]))
            op_time = next((t for t in times if any(s.is_opening for s in by_time[t])), times[0])
            imp_open = consensus(op_time)
            imp_last = consensus(times[-1])
            if not imp_open or not imp_last: continue
            fav_last = int(np.argmax(imp_last))
            cold = 1 if fav_last != actual else 0

            htid, atid = teams.get(mid, (None, None))
            rk_h = rank_by_team.get(htid) if htid else None
            rk_a = rank_by_team.get(atid) if atid else None
            same_league = bool(rk_h and rk_a and rk_h.get("league_id") == rk_a.get("league_id") and rk_h.get("league_id"))
            rank_gap = (rk_h["position"] - rk_a["position"]) if (rk_h and rk_a) else None
            pts_gap = (rk_h["points"] - rk_a["points"]) if (rk_h and rk_a and rk_h.get("points") is not None and rk_a.get("points") is not None) else None

            # 冲突 = 市场热门方向 vs 排名优势方向相反
            rank_support = None
            if fav_last in (0, 2) and rank_gap is not None and same_league:
                rank_support = (fav_last == 0) == (rank_gap < 0)
            pts_support = None
            if fav_last in (0, 2) and pts_gap is not None and same_league:
                pts_support = (fav_last == 0) == (pts_gap > 0)

            samples.append({
                "match_id": mid, "home": m.home_team.name_zh if m.home_team else (m.home_team_name or ""),
                "away": m.away_team.name_zh if m.away_team else (m.away_team_name or ""),
                "actual": actual, "fav_last": fav_last, "cold": cold,
                "fav_prob": imp_last[fav_last],
                "rank_gap": rank_gap, "pts_gap": pts_gap,
                "rank_support": rank_support, "pts_support": pts_support,
                "same_league_rank": same_league,
            })

        sub = [s for s in samples if s["rank_support"] is not None]
        N = len(sub)
        base = sum(s["cold"] for s in sub)/N
        log(f"同联赛排名子样本 n={N}，基线冷门率 {base:.1%}")
        log("")

        # 1) gap 阈值扫描
        log("1. rank_gap 阈值扫描（冲突 = 市场热门 vs 排名优势相反，且 |gap|>=thr）:")
        for thr in [0, 1, 2, 3, 4, 5]:
            g = [s for s in sub if s["rank_support"] is False and abs(s["rank_gap"]) >= thr]
            n_ = [s for s in sub if not (s["rank_support"] is False and abs(s["rank_gap"]) >= thr)]
            if not g or not n_: 
                log(f"  thr={thr}: 样本不足 (g={len(g)} n={len(n_)})")
                continue
            rg = sum(s["cold"] for s in g)/len(g); rn = sum(s["cold"] for s in n_)/len(n_)
            pp = fisher_exact([[sum(s['cold'] for s in g), len(g)-sum(s['cold'] for s in g)],
                               [sum(s['cold'] for s in n_), len(n_)-sum(s['cold'] for s in n_)]], alternative="two-sided")[1]
            log(f"  冲突|gap|>={thr}: n={len(g)} 冷门率 {rg:.1%} vs 其余 {len(n_)}场 {rn:.1%} | p={pp:.4f}")
        log("")

        # 2) 主/客热门拆分（thr=1 全冲突）
        confl = [s for s in sub if s["rank_support"] is False]
        log("2. 主/客热门拆分（全冲突 n=%d）:" % len(confl))
        for fav_dir, label in [(0, "主队热门+客队排名优"), (2, "客队热门+主队排名优")]:
            g = [s for s in confl if s["fav_last"] == fav_dir]
            if g:
                colds = sum(s["cold"] for s in g)
                draws = sum(1 for s in g if s["actual"] == 1)
                log(f"  {label}: n={len(g)} 冷门率 {colds/len(g):.1%} | 其中平局 {draws} ({draws/len(g):.0%})")
        log("")

        # 3) points 口径对照
        sub_p = [s for s in samples if s["pts_support"] is not None]
        confl_p = [s for s in sub_p if s["pts_support"] is False]
        nonc_p = [s for s in sub_p if s["pts_support"] is True]
        if confl_p and nonc_p:
            r1 = sum(s["cold"] for s in confl_p)/len(confl_p); r2 = sum(s["cold"] for s in nonc_p)/len(nonc_p)
            pp = fisher_exact([[sum(s['cold'] for s in confl_p), len(confl_p)-sum(s['cold'] for s in confl_p)],
                               [sum(s['cold'] for s in nonc_p), len(nonc_p)-sum(s['cold'] for s in nonc_p)]], alternative="two-sided")[1]
            log(f"3. points 口径冲突: n={len(confl_p)} 冷门率 {r1:.1%} vs 一致 {len(nonc_p)}场 {r2:.1%} | p={pp:.4f}")
        log("")

        # 4) 冷门构成（冲突场次里的平局 vs 直接输）
        log("4. 冲突场次冷门构成:")
        cold_confl = [s for s in confl if s["cold"]]
        draws = sum(1 for s in cold_confl if s["actual"] == 1)
        log(f"  冲突冷门 {len(cold_confl)} 场：热门平 {draws} ({draws/len(cold_confl):.0%}) | 热门直接输 {len(cold_confl)-draws} ({(len(cold_confl)-draws)/len(cold_confl):.0%})")
        log("")

        # 5) 冲突场次中「排名优势方」胜率（非平局时是否倾向优势方）
        log("5. 冲突场次排名优势方表现:")
        win_lose = [s for s in confl if s["actual"] != 1]
        if win_lose:
            adv_win = 0
            for s in win_lose:
                adv_home = (s["rank_gap"] < 0)
                adv_win += (s["actual"] == 0) if adv_home else (s["actual"] == 2)
            log(f"  非平局 {len(win_lose)} 场：排名优势方直接取胜 {adv_win} ({adv_win/len(win_lose):.0%})")

    with open(r"e:\zhangxuejun\new-thinking\ricking-03\backend\tools\_out_cold_factor_v2_robust2.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))

asyncio.run(main())
