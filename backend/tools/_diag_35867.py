"""查 35867 当前持有者 + 检查是否有部分修改"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    t = await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id, league_id, needs_review, review_reason FROM teams WHERE sportmonks_id=35867")
    print(f"SM 35867 持有者: {dict(t) if t else '无'}")

    t = await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE id=402")
    print(f"402: {dict(t)}")

    # 检查 15629 是否被改过
    t = await conn.fetchrow("SELECT id, home_team_id, sportmonks_fixture_id AS fx FROM matches WHERE id=15629")
    print(f"15629: {dict(t)}")

    await conn.close()


asyncio.run(main())
