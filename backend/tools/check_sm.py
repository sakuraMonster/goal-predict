import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from app.db.database import async_session
from app.db.models import Team
from sqlalchemy import select
async def main():
    async with async_session() as db:
        for tid in [282, 852, 565]:
            r = await db.execute(select(Team).where(Team.id == tid))
            t = r.scalar_one_or_none()
            print(f'{t.name_zh}: SM={t.sportmonks_id}, name_en={t.name_en}')
        # 查谁占用了关键SM ID
        for sm in [2617, 1870, 11914]:
            r = await db.execute(select(Team).where(Team.sportmonks_id == sm))
            t = r.scalar_one_or_none()
            print(f'SM={sm}: {t.name_zh if t else "未使用"} (id={t.id if t else "-"})')
asyncio.run(main())
