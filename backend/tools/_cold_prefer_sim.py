"""搏冷优选模拟：按「排名冲突」规则做每日优选

规则（方向1已验证信号）：
  冲突 = 同联赛排名场次中，市场热门方向(收盘隐含概率 argmax) 与 排名优势方向 相反
  优选 = 每日按 |rank_gap| 从大到小排序，取冲突场次（模拟每日搏冷优选输出）

输出：_out_cold_prefer_sim.txt
"""
import asyncio, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot, TeamSeasonStats

OUT = []
def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

W_START = datetime(2026, 7, 16, 12, 0)
W_END = datetime(2026, 8, 15, 12, 0)
DIRS = {0: "主胜", 1: "平", 2: "客胜"}

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
        league_n = {p.match_id: (m.league.name_zh if m.league else "?") for p, m in rows}

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
                    rank_by_team[key] = {"position": st.league_position, "league_id": st.league_id}

        picks = []
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
            imp_last = consensus(times[-1])
            if not imp_last: continue
            fav_last = int(np.argmax(imp_last))
            cold = 1 if fav_last != actual else 0

            htid, atid = teams.get(mid, (None, None))
            rk_h = rank_by_team.get(htid) if htid else None
            rk_a = rank_by_team.get(atid) if atid else None
            same_league = bool(rk_h and rk_a and rk_h.get("league_id") == rk_a.get("league_id") and rk_h.get("league_id"))
            if not same_league or fav_last not in (0, 2):
                continue
            rank_gap = rk_h["position"] - rk_a["position"]
            support = (fav_last == 0) == (rank_gap < 0)
            if support:
                continue  # 只收冲突
            adv_home = rank_gap < 0
            adv_dir = 0 if adv_home else 2
            picks.append({
                "match_id": mid, "kickoff": kt, "league": league_n.get(mid, "?"),
                "home": m.home_team.name_zh if m.home_team else (m.home_team_name or ""),
                "away": m.away_team.name_zh if m.away_team else (m.away_team_name or ""),
                "fav_last": fav_last, "actual": actual, "cold": cold,
                "fav_prob": imp_last[fav_last], "rank_gap": abs(rank_gap),
                "adv_dir": adv_dir, "adv_hit": (adv_dir == actual),
            })

        N = len(picks)
        log(f"30天窗口内「排名冲突」候选场次: {N}")
        log("")

        # 每日优选模拟：按天分组，每天按 |rank_gap| 排序取 top1/top2
        by_day = defaultdict(list)
        for p in picks:
            by_day[p["kickoff"].date()].append(p)
        days = sorted(by_day.keys())
        log(f"涉及比赛日: {len(days)} 天")
        for k in [1, 2, 3]:
            total = 0; colds = 0; adv_hits = 0; cnt = 0
            for d in days:
                day_picks = sorted(by_day[d], key=lambda x: -x["rank_gap"])[:k]
                for p in day_picks:
                    total += 1; colds += p["cold"]; adv_hits += p["adv_hit"]
            # 有冲突的日子才输出
            days_with = [d for d in days if by_day[d]]
            per_day = len(picks) / len(days_with) if days_with else 0
            log(f"每日 top{k}: 命中 {total} 场，冷门 {colds} ({colds/total:.1%}) | 排名优势方命中 {adv_hits} ({adv_hits/total:.1%})")
        log("")
        log(f"对照：样本基线冷门率 52.1%；冲突场次冷门率 {sum(p['cold'] for p in picks)/N:.1%}")
        log("")

        # 全部冲突场次明细（供人工核验）
        log("===== 全部冲突候选场次 =====")
        for p in sorted(picks, key=lambda x: (x["kickoff"], -x["rank_gap"])):
            log(f"  [{p['kickoff']:%m-%d %H:%M}] {p['league']} {p['home']} vs {p['away']} | "
                f"热门={DIRS[p['fav_last']]}({p['fav_prob']:.2f}) 排名差={p['rank_gap']} 优势方={'主' if p['adv_dir']==0 else '客'} | "
                f"实际={DIRS[p['actual']]} | {'冷' if p['cold'] else '非冷'} 优势方{'✓' if p['adv_hit'] else '✗'}")

    with open(r"e:\zhangxuejun\new-thinking\ricking-03\backend\tools\_out_cold_prefer_sim.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))

asyncio.run(main())
