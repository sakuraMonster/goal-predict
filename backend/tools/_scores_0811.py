"""08-11 竞彩周期 3 场比赛实际比分 + 基本面速览"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match


async def main():
    async with async_session() as db:
        for mid in [15578, 15579, 15580]:
            r = await db.execute(
                select(Prediction).options(joinedload(Prediction.match)).where(Prediction.match_id == mid)
            )
            p = r.unique().scalar_one_or_none()
            m = p.match
            print(f"{mid}: {m.match_num} {m.home_team_name} vs {m.away_team_name} "
                  f"| {m.home_score}:{m.away_score} (半场 {m.half_home_score}:{m.half_away_score}) "
                  f"| 实际总进球={p.actual_total_goals} | λc={p.expected_goals_c:.2f} top2c={p.snap_top2_c}")


asyncio.run(main())
