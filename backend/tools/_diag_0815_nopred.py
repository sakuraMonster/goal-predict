"""排查 08-15 周期无预测记录原因：task_logs + 预测任务状态"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== task_logs 最近 40 条（08-14 起）===")
    rows = await conn.fetch(
        """
        SELECT task_type, status, start_time, end_time, duration_ms, left(message, 150) AS msg
        FROM task_logs
        WHERE start_time >= '2026-08-14 00:00:00'
        ORDER BY start_time DESC LIMIT 40
        """
    )
    for r in rows:
        print(f"  {r['start_time']}  {r['task_type']:<28} {r['status']:<10} {r['duration_ms']}ms | {r['msg'] or ''}")

    print("\n=== 08-15 周期比赛预测记录检查 ===")
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.status,
               EXISTS (SELECT 1 FROM predictions p WHERE p.match_id = m.id) AS has_pred,
               EXISTS (SELECT 1 FROM goal_pick_records g WHERE g.match_id = m.id) AS has_pick
        FROM matches m
        WHERE m.kickoff_time >= '2026-08-15 12:00:00' AND m.kickoff_time < '2026-08-16 12:00:00'
        ORDER BY m.kickoff_time
        """
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_num']:<8} {r['status']:<10} pred={r['has_pred']} pick={r['has_pick']}")

    print("\n=== goal_pick_records 最近记录 ===")
    rows = await conn.fetch(
        """
        SELECT pick_date, rank, match_id, match_num, league_name, home_team, away_team, kickoff_time, expected_goals_c, snap_top2_c, score
        FROM goal_pick_records ORDER BY pick_date DESC, rank LIMIT 20
        """
    )
    for r in rows:
        print(f"  {r['pick_date']} #{r['rank']} match={r['match_id']} {r['match_num']} [{r['league_name']}] "
              f"{r['home_team']} vs {r['away_team']} ko={r['kickoff_time']} γC={r['expected_goals_c']} snap={r['snap_top2_c']} score={r['score']}")

    await conn.close()


asyncio.run(main())
