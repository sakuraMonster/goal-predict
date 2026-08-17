"""08-14 ~ 08-16 三个比赛日深入复盘分析（只读，不写库）"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

from app.db.database import async_session
from app.db.models import Prediction, Match, GoalPickRecord, ColdPickRecord, OddsSnapshot

BEIJING_TZ = timezone(timedelta(hours=8))

DAYS = ["2026-08-14", "2026-08-15", "2026-08-16"]


def date_range(d):
    start = datetime.strptime(d, "%Y-%m-%d").replace(hour=12)
    end = start + timedelta(days=1)
    return start, end


def judge_goals_snap(actual_total, snap):
    """判断进球是否命中某 SNAP 集合"""
    if not snap:
        return None
    return 1 if actual_total in list(snap) else -1


async def main():
    async with async_session() as db:
        for d in DAYS:
            start, end = date_range(d)
            print(f"\n{'='*90}")
            print(f"比赛日 {d}  [{start:%m-%d %H:%M} ~ {end:%m-%d %H:%M})")
            print('='*90)

            # 该比赛日所有 Prediction
            result = await db.execute(
                select(Prediction)
                .options(
                    joinedload(Prediction.match).joinedload(Match.home_team),
                    joinedload(Prediction.match).joinedload(Match.away_team),
                    joinedload(Prediction.match).joinedload(Match.league),
                )
                .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
                .order_by(Prediction.kickoff_time)
            )
            preds = list(result.unique().scalars().all())
            print(f"预测记录总数: {len(preds)}")

            # 已结算 vs 未结算
            settled = [p for p in preds if p.actual_score is not None]
            unsettled = [p for p in preds if p.actual_score is None]
            print(f"已结算: {len(settled)}  未结算: {len(unsettled)}")

            # 各玩法命中统计（用 DB 已回写 result_* 字段，已结算口径）
            spf_hit = sum(1 for p in settled if p.result_spf == 1)
            spf_miss = sum(1 for p in settled if p.result_spf == -1)
            hcp_hit = sum(1 for p in settled if p.result_hcp == 1)
            hcp_miss = sum(1 for p in settled if p.result_hcp == -1)
            goals_hit = sum(1 for p in settled if p.result_goals == 1)
            goals_miss = sum(1 for p in settled if p.result_goals == -1)
            score_hit = sum(1 for p in settled if p.result_score == 1)
            score_miss = sum(1 for p in settled if p.result_score == -1)

            # Model C SNAP 命中（进球数优选口径，snap_top2_c）
            c_hit = 0
            c_miss = 0
            c_na = 0
            for p in settled:
                at = p.actual_total_goals
                if at is None:
                    at = (p.actual_home_score or 0) + (p.actual_away_score or 0)
                r = judge_goals_snap(at, p.snap_top2_c)
                if r == 1:
                    c_hit += 1
                elif r == -1:
                    c_miss += 1
                else:
                    c_na += 1

            def rate(h, m):
                t = h + m
                return f"{h}/{t} = {h/t*100:.1f}%" if t else "N/A"

            print(f"\n[命中率] 胜平负 {rate(spf_hit, spf_miss)}  "
                  f"让球 {rate(hcp_hit, hcp_miss)}  "
                  f"进球数(ModelB) {rate(goals_hit, goals_miss)}  "
                  f"比分Top5 {rate(score_hit, score_miss)}")
            print(f"[命中率] 进球数优选(ModelC snap_top2_c) {rate(c_hit, c_miss)} (无C快照 {c_na})")

            # 冷门场次表现
            cold = [p for p in settled if p.is_cold_match]
            print(f"\n[冷门预警] 标记冷门 {len(cold)} 场（其中已结算 {len(cold)}）")
            if cold:
                cold_hit = sum(1 for p in cold if p.result_spf == 1)
                cold_upset = sum(1 for p in cold if p.result_spf == -1)  # 预测方向未中=实际爆冷
                print(f"  冷门场次中：预测命中 {cold_hit}，预测未中(爆冷) {cold_upset}")

            # 高置信度场次
            high = [p for p in settled if p.confidence_level == "high"]
            if high:
                hh = sum(1 for p in high if p.result_spf == 1)
                hm = sum(1 for p in high if p.result_spf == -1)
                print(f"[高置信度] {len(high)} 场，胜平负命中 {rate(hh, hm)}")

            # 联赛维度
            league_stat = {}
            for p in settled:
                ln = p.league.name_zh if p.league else "未知"
                s = league_stat.setdefault(ln, {"n": 0, "spf_h": 0, "goals_h": 0})
                s["n"] += 1
                if p.result_spf == 1:
                    s["spf_h"] += 1
                if p.result_goals == 1:
                    s["goals_h"] += 1
            print(f"\n[联赛维度]")
            for ln in sorted(league_stat, key=lambda x: -league_stat[x]["n"]):
                s = league_stat[ln]
                print(f"  {ln}: {s['n']}场 胜平负{s['spf_h']}/{s['n']} 进球{s['goals_h']}/{s['n']}")

            # 具体错判场次（胜平负）
            print(f"\n[胜平负错判场次]")
            for p in settled:
                if p.result_spf == -1:
                    m = p.match
                    hn = m.home_team.name_zh if (m and m.home_team) else (m.home_team_name if m else "")
                    an = m.away_team.name_zh if (m and m.away_team) else (m.away_team_name if m else "")
                    probs = {"主": p.home_prob or 0, "平": p.draw_prob or 0, "客": p.away_prob or 0}
                    pred_dir = max(probs, key=probs.get)
                    print(f"  {p.kickoff_time:%m-%d %H:%M} {hn} vs {an}  预测[{pred_dir} {max(probs.values()):.0%}] 实际 {p.actual_score}  (冷={p.is_cold_match}, 置信={p.confidence_level})")

            # 进球数错判场次（Model C 口径）
            print(f"\n[进球数优选(ModelC)错判场次]")
            for p in settled:
                at = p.actual_total_goals
                if at is None:
                    at = (p.actual_home_score or 0) + (p.actual_away_score or 0)
                r = judge_goals_snap(at, p.snap_top2_c)
                if r == -1:
                    m = p.match
                    hn = m.home_team.name_zh if (m and m.home_team) else (m.home_team_name if m else "")
                    an = m.away_team.name_zh if (m and m.away_team) else (m.away_team_name if m else "")
                    print(f"  {p.kickoff_time:%m-%d %H:%M} {hn} vs {an}  λc={p.expected_goals_c} SNAP_c={p.snap_top2_c} 实际总进球 {at}")

        # ── 进球数优选推荐快照表现（跨三天） ──
        print(f"\n{'='*90}\n进球数优选推荐（goal_pick_records）")
        print('='*90)
        from datetime import date as _date
        _picks = [_date(2026, 8, 14), _date(2026, 8, 15), _date(2026, 8, 16)]
        gp = await db.execute(
            select(GoalPickRecord).where(GoalPickRecord.pick_date.in_(_picks))
            .order_by(GoalPickRecord.pick_date, GoalPickRecord.rank)
        )
        gps = list(gp.scalars().all())
        print(f"推荐条数: {len(gps)}")
        for g in gps:
            # 查对应 Prediction 的赛果
            pr = await db.execute(select(Prediction).where(Prediction.match_id == g.match_id))
            pp = pr.scalar_one_or_none()
            if pp and pp.actual_total_goals is not None:
                hit = judge_goals_snap(pp.actual_total_goals, g.snap_top2_c)
                mark = "✓" if hit == 1 else "✗"
            elif pp and pp.actual_home_score is not None:
                at = pp.actual_home_score + pp.actual_away_score
                hit = judge_goals_snap(at, g.snap_top2_c)
                mark = "✓" if hit == 1 else "✗"
            else:
                mark = "未结算"
            print(f"  {g.pick_date} #{g.rank} {g.league_name} {g.home_team} vs {g.away_team}  λc={g.expected_goals_c} SNAP={g.snap_top2_c} score={g.score}  -> {mark}")

        # ── 冷门优选推荐快照表现（跨三天） ──
        print(f"\n{'='*90}\n冷门优选推荐（cold_pick_records）")
        print('='*90)
        cp = await db.execute(
            select(ColdPickRecord).where(ColdPickRecord.pick_date.in_(_picks))
            .order_by(ColdPickRecord.pick_date, ColdPickRecord.rank)
        )
        cps = list(cp.scalars().all())
        print(f"推荐条数: {len(cps)}")
        for c in cps:
            pr = await db.execute(select(Prediction).where(Prediction.match_id == c.match_id))
            pp = pr.scalar_one_or_none()
            if pp and pp.actual_score is not None:
                # 命中口径：实际赛果 != 市场热门方向
                fav_map = {0: "主胜", 1: "平", 2: "客胜"}
                ah, aa = pp.actual_home_score, pp.actual_away_score
                if ah > aa:
                    actual_dir = "主胜"
                elif ah < aa:
                    actual_dir = "客胜"
                else:
                    actual_dir = "平"
                upset = actual_dir != fav_map.get(c.fav_dir, "")
                mark = "✓(爆冷)" if upset else "✗(未爆冷)"
                print(f"  {c.pick_date} #{c.rank} {c.league_name} {c.home_team} vs {c.away_team}  市场热门={fav_map.get(c.fav_dir)} gap={c.rank_gap} score={c.score} 实际={pp.actual_score}({actual_dir}) -> {mark}")
            else:
                print(f"  {c.pick_date} #{c.rank} {c.league_name} {c.home_team} vs {c.away_team}  市场热门={fav_map.get(c.fav_dir)} gap={c.rank_gap} score={c.score}  -> 未结算")


if __name__ == "__main__":
    asyncio.run(main())
