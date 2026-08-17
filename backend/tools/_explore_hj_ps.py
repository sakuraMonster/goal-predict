"""探查荷甲/葡超近60天数据情况：场次、预测、球队统计、盘口数据可用性"""
import asyncio
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction, League, TeamSeasonStats, OddsSnapshot


async def main():
    async with async_session() as db:
        # 1. 查找荷甲/葡超联赛
        result = await db.execute(
            select(League).where(League.name_zh.in_(["荷甲", "葡超"]))
        )
        leagues = result.scalars().all()
        for lg in leagues:
            print(f"联赛: id={lg.id} name_zh={lg.name_zh} name_en={lg.name_en} sportmonks_id={lg.sportmonks_id}")

        # 2. 近60天 (06-11 ~ 08-10) 这两个联赛的比赛
        end = datetime(2026, 8, 10, 12, 0, 0)
        start = end - timedelta(days=60)
        result = await db.execute(
            select(Match)
            .options(joinedload(Match.league))
            .where(
                Match.kickoff_time >= start,
                Match.kickoff_time < end,
                Match.league_id.in_([lg.id for lg in leagues]),
            )
            .order_by(Match.kickoff_time)
        )
        matches = list(result.unique().scalars().all())
        print(f"\n近60天 {[lg.name_zh for lg in leagues]} 共 {len(matches)} 场比赛")

        for m in matches:
            pred_result = await db.execute(
                select(Prediction).where(Prediction.match_id == m.id)
            )
            pred = pred_result.scalar_one_or_none()
            actual = pred.actual_total_goals if pred else None
            print(
                f"  [{m.kickoff_time.strftime('%m-%d %H:%M')}] id={m.id} "
                f"{m.home_team_name} vs {m.away_team_name} "
                f"实际={actual}球 (score={pred.actual_score if pred else None})"
                f" result_goals={pred.result_goals if pred else None}"
            )

        # 3. 球队统计可用性
        print(f"\n球队统计可用性:")
        team_ids = set()
        for m in matches:
            if m.home_team_id:
                team_ids.add(m.home_team_id)
            if m.away_team_id:
                team_ids.add(m.away_team_id)
        for tid in sorted(team_ids)[:20]:
            ts = await db.execute(
                select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid).order_by(TeamSeasonStats.season)
            )
            stats_list = ts.scalars().all()
            if stats_list:
                for stats in stats_list:
                    print(
                        f"  team_id={tid} season={stats.season} played={stats.played} gf={stats.goals_for} "
                        f"ga={stats.goals_against} league_id={stats.league_id} pos={stats.league_position}"
                    )
            else:
                print(f"  team_id={tid} 无赛季统计!")

        # 4. 盘口数据可用性（大小球）
        print(f"\n盘口数据可用性:")
        for m in matches[:10]:
            odds_cnt = await db.execute(
                select(func.count(OddsSnapshot.id)).where(OddsSnapshot.match_id == m.id)
            )
            cnt = odds_cnt.scalar_one()
            bm_cnt = await db.execute(
                select(func.count(func.distinct(OddsSnapshot.bookmaker)))
                .where(OddsSnapshot.match_id == m.id)
            )
            bm = bm_cnt.scalar_one()
            gl = await db.execute(
                select(func.count(OddsSnapshot.id)).where(
                    OddsSnapshot.match_id == m.id,
                    OddsSnapshot.goal_line.isnot(None),
                )
            )
            gl_cnt = gl.scalar_one()
            print(f"  id={m.id} {m.home_team_name}vs{m.away_team_name}: 赔率快照={cnt} 博彩公司={bm} 有大小球盘={gl_cnt}")


asyncio.run(main())
