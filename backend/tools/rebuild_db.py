"""重建数据库表（匹配最新 ORM 模型）"""
import asyncio
import asyncpg
from app.db.database import engine, Base
import app.db.models  # noqa: F401


async def rebuild():
    conn = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost/football_prediction"
    )
    await conn.execute("""
        DROP TABLE IF EXISTS league_aliases, team_aliases, predictions,
        odds_snapshots, injuries, head_to_head, team_season_stats,
        task_logs, matches, teams, leagues CASCADE
    """)
    await conn.execute("DROP TABLE IF EXISTS _migrations, team_stats CASCADE")
    await conn.close()
    print("旧表已清除")

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)

    conn = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost/football_prediction"
    )
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    print(f"重建完成，{len(tables)} 张表: {[t['tablename'] for t in tables]}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(rebuild())
