"""cold_pick_records 表新增盘口资金异动特征列（幂等 ALTER）"""
import asyncio
from sqlalchemy import text
from app.db.database import engine


async def main():
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE cold_pick_records ADD COLUMN IF NOT EXISTS "
            "odds_delta_max DOUBLE PRECISION"
        ))
        await conn.execute(text(
            "ALTER TABLE cold_pick_records ADD COLUMN IF NOT EXISTS "
            "odds_step_max DOUBLE PRECISION"
        ))
    async with engine.connect() as conn:
        r = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='cold_pick_records' ORDER BY ordinal_position"
        ))
        cols = [row[0] for row in r.fetchall()]
        print("cold_pick_records 列:", cols)


if __name__ == "__main__":
    asyncio.run(main())
