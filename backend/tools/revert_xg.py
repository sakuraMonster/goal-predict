import asyncio, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.db.database import engine
from app.db.models import TeamSeasonStats

async def c():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as db:
        r = await db.execute(update(TeamSeasonStats).values(xG=None, xGA=None))
        await db.commit()
        print(f'xG/xGA 全部重置为 NULL, {r.rowcount} 条')

asyncio.run(c())
