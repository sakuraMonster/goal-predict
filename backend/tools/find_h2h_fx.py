import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import HeadToHead
from sqlalchemy import select

async def main():
    async with async_session() as db:
        h2hs = (await db.execute(select(HeadToHead))).scalars().all()
        # 找一个有 sm_fixture_id 的记录
        for h in h2hs:
            if h.sportmonks_fixture_id:
                print(f"fx_id={h.sportmonks_fixture_id} date={h.match_date.date()} teams={h.home_team_id}/{h.away_team_id} score={h.home_score}:{h.away_score}")
                print(f"  已存stats keys: {list(h.home_stats.keys()) if h.home_stats else 'None'}")

asyncio.run(main())
