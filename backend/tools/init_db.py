"""初始化本地 PostgreSQL 数据库"""
import asyncio
import asyncpg
from app.db.database import engine, Base
import app.db.models  # noqa: F401


async def init():
    # 先连接默认库，创建 football_prediction
    sys_conn = await asyncpg.connect("postgresql://postgres:postgres@localhost/postgres")

    exists = await sys_conn.fetchval(
        "SELECT 1 FROM pg_database WHERE datname='football_prediction'"
    )
    if not exists:
        await sys_conn.execute("CREATE DATABASE football_prediction")
        print("数据库 football_prediction 已创建")
    else:
        print("数据库 football_prediction 已存在")
    await sys_conn.close()

    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 验证
    conn = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost/football_prediction"
    )
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    print(f"已创建 {len(tables)} 张表:")
    for t in tables:
        print(f"  OK {t['tablename']}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(init())
