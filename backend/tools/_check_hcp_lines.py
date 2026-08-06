"""检查所有比赛的手 handicap_line"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from app.db.database import async_session
from app.db.models import Match, Team
from sqlalchemy import select

async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Match).where(Match.kickoff_time >= '2026-07-31', Match.match_num.isnot(None))
            .order_by(Match.kickoff_time)
        )
        for m in r.scalars().all():
            ht = await db.execute(select(Team).where(Team.id == m.home_team_id))
            at = await db.execute(select(Team).where(Team.id == m.away_team_id))
            ht = ht.scalar_one()
            at = at.scalar_one()
            print(f"{m.match_num}: {ht.name_zh} vs {at.name_zh}, hcp_line={m.handicap_line}")

asyncio.run(main())
