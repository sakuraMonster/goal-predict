"""
数据库迁移: 将 source='500.com' 更新为 'sporttery.cn'
平滑迁移已有数据，不删除任何记录。
"""
import asyncio
from app.db.database import engine
from sqlalchemy import text


async def migrate():
    async with engine.begin() as conn:
        print("=" * 60)
        print("迁移 500.com → sporttery.cn")
        print("=" * 60)

        # 1. team_aliases: 更新 source 字段
        result = await conn.execute(
            text("UPDATE team_aliases SET source = 'sporttery.cn' WHERE source = '500.com'")
        )
        print(f"\n1. team_aliases: 更新 {result.rowcount} 条记录 (source: 500.com → sporttery.cn)")

        # 2. league_aliases: 更新 source 字段
        result = await conn.execute(
            text("UPDATE league_aliases SET source = 'sporttery.cn' WHERE source = '500.com'")
        )
        print(f"2. league_aliases: 更新 {result.rowcount} 条记录 (source: 500.com → sporttery.cn)")

        # 3. teams: 更新 review_reason 文本
        result = await conn.execute(
            text("UPDATE teams SET review_reason = REPLACE(review_reason, '从500.com自动新增', '从竞彩网自动新增') WHERE review_reason LIKE '%500.com%'")
        )
        print(f"3. teams: 更新 {result.rowcount} 条记录 (review_reason: 500.com → 竞彩网)")

        print("\n迁移完成!")


if __name__ == "__main__":
    asyncio.run(migrate())
