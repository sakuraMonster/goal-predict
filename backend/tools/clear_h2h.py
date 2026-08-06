"""清除旧 H2H 数据"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import engine
from sqlalchemy import text

async def main():
    async with engine.begin() as c:
        r = await c.execute(text("DELETE FROM head_to_head"))
        print(f"Deleted {r.rowcount} H2H records")

asyncio.run(main())
