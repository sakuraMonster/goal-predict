"""检查 task_logs 近48小时所有任务，判断 update_teams 为何未在 08-15 03:00 运行"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """
        SELECT task_type, status, start_time, end_time, duration_ms, left(message,150) AS msg
        FROM task_logs
        WHERE start_time >= '2026-08-14 00:00:00'
        ORDER BY start_time
        """
    )
    for r in rows:
        print(f"  {r['start_time']}  {r['task_type']:<24} {r['status']:<10} {r['duration_ms']}ms | {r['msg'] or ''}")

    print("\n=== 03:00 附近的日志（08-14/08-15 各 03:00）===")
    for day in ["2026-08-14", "2026-08-15"]:
        rows = await conn.fetch(
            """
            SELECT task_type, status, start_time, left(message,200) AS msg
            FROM task_logs
            WHERE start_time >= $1::timestamp - interval '1 hour'
              AND start_time < $1::timestamp + interval '2 hours'
            ORDER BY start_time
            """,
            f"{day} 03:00:00",
        )
        print(f"  --- {day} 02:00~05:00 ---")
        for r in rows:
            print(f"    {r['start_time']} {r['task_type']} {r['status']} | {r['msg'] or ''}")

    await conn.close()


asyncio.run(main())
