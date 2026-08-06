import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import HeadToHead, Team
from sqlalchemy import select

async def main():
    async with async_session() as db:
        # KuPS-Sabah H2H
        h2hs = (await db.execute(select(HeadToHead).where(
            ((HeadToHead.home_team_id==146)&(HeadToHead.away_team_id==125)) |
            ((HeadToHead.home_team_id==125)&(HeadToHead.away_team_id==146))
        ))).scalars().all()
        for h in h2hs:
            print(f"date={h.match_date.date()} fx_id={h.sportmonks_fixture_id}")
            print(f"  home={h.home_score}:{h.away_score}")

asyncio.run(main())
