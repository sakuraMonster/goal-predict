"""诊断 predictions 历史数据量"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    total = await conn.fetchval("SELECT count(*) FROM predictions")
    settled = await conn.fetchval("SELECT count(*) FROM predictions WHERE actual_total_goals IS NOT NULL")
    has_c = await conn.fetchval("SELECT count(*) FROM predictions WHERE snap_top2_c IS NOT NULL")
    both = await conn.fetchval("SELECT count(*) FROM predictions WHERE actual_total_goals IS NOT NULL AND snap_top2_c IS NOT NULL")
    both_b = await conn.fetchval("SELECT count(*) FROM predictions WHERE actual_total_goals IS NOT NULL AND snap_top2 IS NOT NULL")
    kmin = await conn.fetchval("SELECT min(kickoff_time) FROM predictions WHERE actual_total_goals IS NOT NULL")
    kmax = await conn.fetchval("SELECT max(kickoff_time) FROM predictions WHERE actual_total_goals IS NOT NULL")
    cmin = await conn.fetchval("SELECT min(kickoff_time) FROM predictions WHERE snap_top2_c IS NOT NULL")
    cmax = await conn.fetchval("SELECT max(kickoff_time) FROM predictions WHERE snap_top2_c IS NOT NULL")

    print(f"总 predictions: {total}")
    print(f"已结算(actual_total_goals非空): {settled}")
    print(f"有 snap_top2_c: {has_c}")
    print(f"已结算且有C: {both}")
    print(f"已结算且有B(snap_top2): {both_b}")
    print(f"已结算 kickoff 范围: {kmin} ~ {kmax}")
    print(f"snap_top2_c kickoff 范围: {cmin} ~ {cmax}")

    # 近30天已结算且有C
    near = await conn.fetchval(
        "SELECT count(*) FROM predictions WHERE actual_total_goals IS NOT NULL AND snap_top2_c IS NOT NULL AND kickoff_time >= $1",
        "2026-07-15 12:00:00")
    print(f"近30天(>=07-15 12:00)已结算且有C: {near}")

    await conn.close()


asyncio.run(main())
