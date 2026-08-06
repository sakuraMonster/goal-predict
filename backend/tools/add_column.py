import asyncio
from sqlalchemy import text
from app.db.database import engine

async def run():
    async with engine.begin() as conn:
        for col in ["sportmonks_fixture_id"]:
            try:
                await conn.execute(text(f'ALTER TABLE matches ADD COLUMN {col} INTEGER'))
                print(f"{col} 列已添加")
            except Exception as e:
                print(f"{col}: {e}")

asyncio.run(run())
