"""08-14~08-16 模型C + 冷门模型 深度分析（只读）"""
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date as _date

from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

from app.db.database import async_session
from app.db.models import Prediction, Match, TeamSeasonStats, OddsSnapshot

BEIJING_TZ = timezone(timedelta(hours=8))
DAYS = ["2026-08-14", "2026-08-15", "2026-08-16"]


def date_range(d):
    start = datetime.strptime(d, "%Y-%m-%d").replace(hour=12)
    return start, start + timedelta(days=1)


def implied(h, d, a):
    if not h or not d or not a or min(h, d, a) <= 1.01:
        return None
    ih, id_, ia = 1 / h, 1 / d, 1 / a
    t = ih + id_ + ia
    return (ih / t, id_ / t, ia / t)


async def main():
    async with async_session() as db:
        # 收集三天所有已结算 prediction + match
        all_preds = []
        for d in DAYS:
            s, e = date_range(d)
            r = await db.execute(
                select(Prediction)
                .options(
                    joinedload(Prediction.match).joinedload(Match.home_team),
                    joinedload(Prediction.match).joinedload(Match.away_team),
                    joinedload(Prediction.match).joinedload(Match.league),
                )
                .where(and_(Prediction.kickoff_time >= s, Prediction.kickoff_time < e))
            )
            all_preds += list(r.unique().scalars().all())

        settled = [p for p in all_preds if p.actual_home_score is not None]

        # ══════════ 模型C 深度 ══════════
        print("=" * 90)
        print("【模型 C 深度分析】")
        print("=" * 90)

        # 实际总进球分布
        dist = defaultdict(int)
        for p in settled:
            tg = (p.actual_home_score or 0) + (p.actual_away_score or 0)
            bucket = "6+" if tg >= 6 else str(tg)
            dist[bucket] += 1
        print(f"\n实际总进球分布（{len(settled)}场）:")
        for k in ["0", "1", "2", "3", "4", "5", "6+"]:
            n = dist[k]
            bar = "█" * n
            print(f"  {k:>2}球: {n:>2}场 {bar}")

        # λc 分布
        lcs = [p.expected_goals_c for p in settled if p.expected_goals_c is not None]
        if lcs:
            lcs_sorted = sorted(lcs)
            import statistics
            print(f"\nModel C λc 分布: 均值={statistics.mean(lcs):.2f} 中位数={statistics.median(lcs):.2f} "
                  f"min={min(lcs):.2f} max={max(lcs):.2f}")

        # 实际场均进球
        total_goals = sum((p.actual_home_score or 0) + (p.actual_away_score or 0) for p in settled)
        print(f"实际场均总进球 = {total_goals}/{len(settled)} = {total_goals/len(settled):.2f}")
        if lcs:
            print(f"Model C 预测场均 λc = {sum(lcs)/len(lcs):.2f}")
            print(f"→ 系统性偏差 = {total_goals/len(settled) - sum(lcs)/len(lcs):+.2f} 球/场")

        # 按联赛：实际 vs λc
        league_agg = defaultdict(lambda: {"n": 0, "act": 0, "lc": 0})
        for p in settled:
            ln = p.league.name_zh if p.league else "未知"
            a = league_agg[ln]
            a["n"] += 1
            a["act"] += (p.actual_home_score or 0) + (p.actual_away_score or 0)
            a["lc"] += p.expected_goals_c or 0
        print(f"\n按联赛 实际场均 vs λc 均值（偏差>0.5 标注）:")
        for ln in sorted(league_agg, key=lambda x: -league_agg[x]["n"]):
            a = league_agg[ln]
            act = a["act"] / a["n"]
            lc = a["lc"] / a["n"]
            flag = " ← 严重低估" if act - lc > 0.5 else (" ← 高估" if act - lc < -0.3 else "")
            print(f"  {ln:<6} n={a['n']:>2}  实际{act:.2f}  λc{lc:.2f}  偏差{act-lc:+.2f}{flag}")

        # ══════════ 冷门模型 深度 ══════════
        print("\n" + "=" * 90)
        print("【冷门模型 深度分析】")
        print("=" * 90)

        # 需要每场的 market 热门方向（收盘隐含概率）+ 排名冲突
        match_ids = [p.match_id for p in settled]
        snaps = (await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id.in_(match_ids))
        )).scalars().all()
        by_match = defaultdict(list)
        for s in snaps:
            kt = next((p.match.kickoff_time for p in settled if p.match_id == s.match_id), None)
            if kt and s.snapshot_time < kt:
                by_match[s.match_id].append(s)

        # 排名
        tids = set()
        for p in settled:
            if p.match and p.match.home_team_id:
                tids.add(p.match.home_team_id)
            if p.match and p.match.away_team_id:
                tids.add(p.match.away_team_id)
        stats = (await db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.team_id.in_(tids))
        )).scalars().all()
        rank_by_team = {}
        for st in stats:
            if st.league_position is not None:
                if st.team_id not in rank_by_team or (st.league_id and not rank_by_team[st.team_id].get("league_id")):
                    rank_by_team[st.team_id] = {"position": st.league_position, "league_id": st.league_id}

        # 对每场算市场热门方向 + 冲突
        base_hit = base_miss = 0
        conflict_upset = conflict_total = 0
        no_conflict_upset = no_conflict_total = 0
        gap_layers = defaultdict(lambda: [0, 0])  # gap分层 -> [爆冷, 总数]

        for p in settled:
            m = p.match
            if not m:
                continue
            # 市场热门（收盘最后时刻 consensus）
            sl = by_match.get(p.match_id)
            if not sl:
                continue
            times = sorted({s.snapshot_time for s in sl})
            if not times:
                continue
            last_t = times[-1]
            spf = {}
            for s in sl:
                if s.snapshot_time != last_t:
                    continue
                bm = s.bookmaker or "unknown"
                if bm not in spf and s.home_win and s.draw and s.away_win:
                    spf[bm] = s
            if not spf:
                continue
            imp = implied(
                sum(s.home_win for s in spf.values()) / len(spf),
                sum(s.draw for s in spf.values()) / len(spf),
                sum(s.away_win for s in spf.values()) / len(spf),
            )
            if not imp:
                continue
            fav = int(max(range(3), key=lambda i: imp[i]))
            if fav not in (0, 2):
                continue  # 平局热门无方向意义

            # 实际赛果方向
            ah, aa = p.actual_home_score, p.actual_away_score
            if ah > aa:
                actual_dir = 0
            elif ah < aa:
                actual_dir = 2
            else:
                actual_dir = 1

            # 市场热门方向命中率（基准）
            if fav == actual_dir:
                base_hit += 1
            else:
                base_miss += 1

            # 排名冲突
            rk_h = rank_by_team.get(m.home_team_id)
            rk_a = rank_by_team.get(m.away_team_id)
            if not (rk_h and rk_a and rk_h.get("league_id") and rk_h.get("league_id") == rk_a.get("league_id")):
                continue
            rank_gap = rk_h["position"] - rk_a["position"]
            conflict = (fav == 0) != (rank_gap < 0)  # True=冲突
            upset = (actual_dir != fav)  # 爆冷

            if conflict:
                conflict_total += 1
                if upset:
                    conflict_upset += 1
            else:
                no_conflict_total += 1
                if upset:
                    no_conflict_upset += 1

            # gap 分层（仅冲突场次）
            if conflict:
                g = abs(rank_gap)
                layer = ">=5" if g >= 5 else (">=4" if g >= 4 else (">=3" if g >= 3 else "<3"))
                gap_layers[layer][1] += 1
                if upset:
                    gap_layers[layer][0] += 1

        print(f"\n市场热门方向命中率（基准，n={base_hit+base_miss}）: {base_hit}/{base_hit+base_miss} = {base_hit/(base_hit+base_miss)*100:.1f}%")
        print(f"  即市场热门方向爆冷率 = {base_miss/(base_hit+base_miss)*100:.1f}%")

        print(f"\n排名冲突 vs 非冲突 爆冷率对比:")
        if conflict_total:
            print(f"  冲突场次: {conflict_upset}/{conflict_total} 爆冷 = {conflict_upset/conflict_total*100:.1f}%")
        if no_conflict_total:
            print(f"  非冲突场次: {no_conflict_upset}/{no_conflict_total} 爆冷 = {no_conflict_upset/no_conflict_total*100:.1f}%")

        print(f"\n冲突场次按 |rank_gap| 分层爆冷率:")
        for layer in ["<3", ">=3", ">=4", ">=5"]:
            u, t = gap_layers[layer]
            if t:
                print(f"  |gap|{layer}: {u}/{t} = {u/t*100:.1f}%")
            else:
                print(f"  |gap|{layer}: 无样本")


if __name__ == "__main__":
    asyncio.run(main())
