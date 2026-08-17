"""查询 update_teams 最近记录 + 进程运行时间"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        "SELECT start_time, status, duration_ms, message FROM task_logs WHERE task_type='update_teams' ORDER BY start_time DESC LIMIT 3"
    )
    for x in rows:
        print(x["start_time"], x["status"], x["duration_ms"], x["message"])
    await conn.close()


asyncio.run(main())
