"""确认调度器运行状态：查 sync_odds / update_teams 近期记录 + 各任务计数"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_sched_check.txt"


async def main():
    conn = await asyncpg.connect(DSN)
    lines = []

    def p(s=""):
        lines.append(str(s))
        print(s)

    p("=== sync_odds 最近 10 条（验证调度器是否在跑）===")
    rows = await conn.fetch(
        """SELECT start_time, status, duration_ms FROM task_logs
           WHERE task_type='sync_odds' ORDER BY start_time DESC LIMIT 10"""
    )
    for r in rows:
        p(f"  {r['start_time']} {r['status']:8s} {r['duration_ms']}ms")

    p("\n=== update_teams 08-15 全天记录 ===")
    rows = await conn.fetch(
        """SELECT start_time, status, duration_ms, message FROM task_logs
           WHERE task_type='update_teams' AND start_time >= '2026-08-15 00:00:00'
           ORDER BY start_time"""
    )
    if not rows:
        p("  (无记录)")
    for r in rows:
        p(f"  {r['start_time']} {r['status']:8s} {r['duration_ms']}ms {r['message']}")

    p("\n=== 08-15 各任务计数 ===")
    rows = await conn.fetch(
        """SELECT task_type, COUNT(*) FROM task_logs
           WHERE start_time >= '2026-08-15 00:00:00' GROUP BY task_type ORDER BY count DESC"""
    )
    for r in rows:
        p(f"  {r['task_type']:20s} {r['count']}")

    p("\n=== 08-15 所有非 api_request 日志（近 200 条内）===")
    rows = await conn.fetch(
        """SELECT start_time, task_type, status, duration_ms, message FROM task_logs
           WHERE start_time >= '2026-08-15 00:00:00' AND task_type != 'api_request'
           ORDER BY start_time DESC LIMIT 40"""
    )
    for r in rows:
        p(f"  {r['start_time']} {r['task_type']:15s} {r['status']:8s} {r['duration_ms']}ms | {r['message']}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    p(f"\n[written] {OUT}")
    await conn.close()


asyncio.run(main())
