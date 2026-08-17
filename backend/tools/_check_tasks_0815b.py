"""精确检查 update_teams 任务日志（08-14 12:00 之后全部）"""
import asyncio
import asyncpg
from datetime import datetime

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """
        SELECT task_type, status, start_time, end_time, duration_ms, left(message,200) AS msg
        FROM task_logs
        WHERE start_time >= $1
        ORDER BY start_time
        """,
        datetime(2026, 8, 14, 12, 0, 0),
    )
    for r in rows:
        if r["task_type"] in ("update_teams", "sync_team_info"):
            print(f"  {r['start_time']} {r['task_type']:<14} {r['status']:<10} {r['duration_ms']}ms | {r['msg'] or ''}")

    print("\n=== 各 task_type 计数 ===")
    rows = await conn.fetch(
        """
        SELECT task_type, status, count(*)
        FROM task_logs
        WHERE start_time >= $1
        GROUP BY task_type, status
        ORDER BY task_type, status
        """,
        datetime(2026, 8, 14, 12, 0, 0),
    )
    for r in rows:
        print(f"  {r['task_type']:<24} {r['status']:<12} {r['count']}")

    await conn.close()


asyncio.run(main())
