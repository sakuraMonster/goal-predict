"""检查表结构和历史数据范围"""
import asyncio
import asyncpg

async def main():
    c = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    # matches 表结构
    cols = await c.fetch("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name = 'matches'
        ORDER BY ordinal_position
    """)
    print("=== matches 表结构 ===")
    for r in cols:
        print(f"  {r['column_name']} ({r['data_type']})")

    print()

    # 所有联赛数据范围
    rows = await c.fetch("""
        SELECT l.name_zh, MIN(m.kickoff_time), MAX(m.kickoff_time), COUNT(*)
        FROM matches m JOIN leagues l ON m.league_id = l.id
        GROUP BY l.name_zh
        ORDER BY MIN(m.kickoff_time) DESC
    """)
    print("=== 所有联赛数据范围 ===")
    for r in rows:
        print(f"  {r[0]:8s}  {r[1]} ~ {r[2]}  ({r[3]}场)")

    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
