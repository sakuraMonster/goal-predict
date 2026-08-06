"""检查映射统计数据"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.db.database import async_session
from sqlalchemy import select, func
from app.db.models import Team

async def main():
    async with async_session() as db:
        # total
        r = await db.execute(select(func.count(Team.id)))
        total = r.scalar() or 0
        print(f"Total teams: {total}")

        # pending by needs_review
        r = await db.execute(
            select(func.count(Team.id)).where((Team.name_zh == None) | (Team.needs_review == True)))
        pending = r.scalar() or 0
        print(f"Pending (name_zh NULL or needs_review=True): {pending}")

        # confirmed
        r = await db.execute(
            select(func.count(Team.id)).where(Team.name_zh != None, Team.needs_review == False))
        confirmed = r.scalar() or 0
        print(f"Confirmed (name_zh NOT NULL and needs_review=False): {confirmed}")
        print(f"Sum check: {pending + confirmed} == {total}")

        # show pending teams detail
        r = await db.execute(
            select(Team).where((Team.name_zh == None) | (Team.needs_review == True)))
        for t in r.scalars().all():
            print(f"  id={t.id} name_zh={t.name_zh} name_en={t.name_en} needs_review={t.needs_review} sm_id={t.sportmonks_id}")

asyncio.run(main())
