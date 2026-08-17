"""查询 DB 当前时间 + predict 任务历史"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    now = await conn.fetchval("SELECT now()")
    print("DB now():", now)

    print("\n=== predict 类任务近 5 天历史 ===")
    rows = await conn.fetch(
        """
        SELECT task_type, status, start_time, duration_ms, left(message, 150) AS msg
        FROM task_logs
        WHERE task_type LIKE '%predict%' OR task_type IN ('goal_picks', 'picks')
        ORDER BY start_time DESC LIMIT 30
        """
    )
    for r in rows:
        print(f"  {r['start_time']}  {r['task_type']:<28} {r['status']:<10} {r['duration_ms']}ms | {r['msg'] or ''}")

    print("\n=== 08-14 周期（08-14 12:00 ~ 08-15 12:00）预测情况 ===")
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, l.name_zh AS lg, m.kickoff_time, m.status,
               p.id AS pid, p.expected_goals_c, p.snap_top2_c, p.model_version
        FROM matches m
        LEFT JOIN leagues l ON l.id = m.league_id
        LEFT JOIN predictions p ON p.match_id = m.id
        WHERE m.kickoff_time >= '2026-08-14 12:00:00' AND m.kickoff_time < '2026-08-15 12:00:00'
        ORDER BY m.kickoff_time
        """
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_num']:<8} [{r['lg']}] ko={r['kickoff_time']} status={r['status']} pred={'Y' if r['pid'] else 'N'} γC={r['expected_goals_c']} snap={r['snap_top2_c']} v={r['model_version']}")

    await conn.close()


asyncio.run(main())
