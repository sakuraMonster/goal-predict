import asyncio, sys
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, League

async def main():
    async with async_session() as db:
        for mid in [15463]:
            mr = await db.execute(select(Match).where(Match.id==mid))
            m = mr.scalar_one_or_none()
            if m:
                lr = await db.execute(select(League).where(League.id==m.league_id))
                league = lr.scalar_one_or_none()
                lname = league.name_zh if league else "?"
                print(f'{m.match_num}: league_id={m.league_id}, league={lname}')

asyncio.run(main())
