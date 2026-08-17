"""检查 task_logs 近期任务状态 + 08-14 周期对照（是否有 fx/赔率）"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== task_logs 近期任务（08-14 起）===")
    rows = await conn.fetch(
        """
        SELECT task_type, status, start_time, duration_ms, left(message,120) AS msg
        FROM task_logs WHERE start_time >= '2026-08-14 00:00:00'
        ORDER BY start_time DESC LIMIT 30
        """
    )
    for r in rows:
        print(f"  {r['start_time']} {r['task_type']:<26} {r['status']:<10} {r['duration_ms']}ms | {r['msg'] or ''}")

    print("\n=== 08-14 比赛周期对照（08-14 12:00 ~ 08-15 12:00）===")
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, l.name_zh, m.kickoff_time, m.sportmonks_fixture_id AS fx,
               (SELECT count(*) FROM odds_snapshots o WHERE o.match_id=m.id) AS odds_cnt
        FROM matches m LEFT JOIN leagues l ON l.id=m.league_id
        WHERE m.kickoff_time >= '2026-08-14 12:00:00' AND m.kickoff_time < '2026-08-15 12:00:00'
        ORDER BY m.kickoff_time
        """
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_num']:<8} [{r['name_zh']}] ko={r['kickoff_time']} fx={r['fx']} odds={r['odds_cnt']}")

    print("\n=== 全部 scheduled + 未来 + 无fx 的比赛（需确认 match_fixtures 是否已跑）===")
    rows = await conn.fetch(
        """
        SELECT count(*) FILTER (WHERE sportmonks_fixture_id IS NULL AND kickoff_time >= now()) AS future_no_fx,
               count(*) FILTER (WHERE sportmonks_fixture_id IS NOT NULL AND kickoff_time >= now()) AS future_with_fx
        FROM matches WHERE status='scheduled'
        """
    )
    for r in rows:
        print(f"  future_no_fx={r['future_no_fx']}  future_with_fx={r['future_with_fx']}")

    await conn.close()


asyncio.run(main())
