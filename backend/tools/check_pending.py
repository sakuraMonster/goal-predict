import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import Team
from sqlalchemy import select

async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Team).where((Team.needs_review == True) | (Team.name_zh == None)).order_by(Team.id)
        )
        teams = r.scalars().all()
        print(f"待确认总数: {len(teams)}")
        for t in teams:
            print(f"  id={t.id:3d}  zh={t.name_zh or '(null)':16s}  en={t.name_en or '(null)':30s}  sm_id={t.sportmonks_id}  reason={t.review_reason}")

asyncio.run(main())
