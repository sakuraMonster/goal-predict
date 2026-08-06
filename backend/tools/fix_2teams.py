"""直接修复2个错误SM ID"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from app.db.database import async_session
from app.db.models import Team
from sqlalchemy import select


async def main():
    async with async_session() as db:
        fixes = [
            (852, 1870, "Orgryte IS"),    # 厄尔格里特 SM 86→1870
            (565, 11914, "KFUM Oslo"),    # 奥斯陆KFUM SM 57→11914
        ]
        for tid, sm, name in fixes:
            r = await db.execute(select(Team).where(Team.id == tid))
            t = r.scalar_one_or_none()
            if t:
                print(f"{t.name_zh}: SM {t.sportmonks_id}→{sm}, name_en '{t.name_en}'→'{name}'")
                t.sportmonks_id = sm
                t.name_en = name
                t.needs_review = False
                t.review_reason = None
        await db.commit()
        print("done")

asyncio.run(main())
