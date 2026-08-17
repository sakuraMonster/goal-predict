"""验证用户最初问题：全北现代 vs 济州FC 的 GL 和 drop"""
import sys, os, asyncio
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import OddsSnapshot, Match

async def main():
    async with async_session() as db:
        res = await db.execute(
            select(Match).where(
                Match.home_team_name.like("%全北%"),
                Match.kickoff_time >= datetime(2026, 8, 8),
            )
        )
        matches = res.scalars().all()
        for m in matches:
            print(f"match {m.id}: {m.home_team_name} vs {m.away_team_name} @ {m.kickoff_time} league={m.league_id}")
            r2 = await db.execute(
                select(OddsSnapshot.snapshot_time, func.count(OddsSnapshot.id),
                       func.min(OddsSnapshot.goal_line), func.max(OddsSnapshot.goal_line))
                .where(OddsSnapshot.match_id == m.id)
                .group_by(OddsSnapshot.snapshot_time).order_by(OddsSnapshot.snapshot_time)
            )
            for t, n, gl_min, gl_max in r2.all():
                print(f"  {t.strftime('%m-%d %H:%M')} n={n} gl=[{gl_min}~{gl_max}]")

asyncio.run(main())
