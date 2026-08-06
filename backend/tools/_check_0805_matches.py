import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match

async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Match)
            .options(joinedload(Match.league))
            .where(Match.id.in_([15511, 15512, 15513]))
        )
        for m in r.unique().scalars().all():
            lg = m.league.name_zh if m.league else "N/A"
            print(f"id={m.id} | {m.home_team_name} vs {m.away_team_name} | league_id={m.league_id} league={lg} | kickoff={m.kickoff_time} | status={m.status}")

asyncio.run(main())
