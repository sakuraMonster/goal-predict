"""08-15/08-16 周期检查：task_logs + SM H2H 实测
验证缺 H2H 的对位是否在 SM 端有数据（判定是数据源缺失还是同步问题）
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== task_logs update_teams 近期运行 ===")
    rows = await conn.fetch(
        """
        SELECT task_type, status, start_time, end_time, duration_ms, left(message,200) AS msg
        FROM task_logs
        WHERE task_type IN ('update_teams','sync_team_info') OR message ILIKE '%交锋%' OR message ILIKE '%球队%'
        ORDER BY start_time DESC LIMIT 12
        """
    )
    for r in rows:
        print(f"  {r['start_time']} {r['task_type']:<14} {r['status']:<10} {r['duration_ms']}ms | {r['msg'] or ''}")

    print("\n=== 缺H2H 比赛对位（team sm_id 双方）===")
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, l.name_zh, th.name_zh AS hzh, th.sportmonks_id AS hsm,
               ta.name_zh AS azh, ta.sportmonks_id AS asm, m.kickoff_time
        FROM matches m
        LEFT JOIN leagues l ON l.id=m.league_id
        LEFT JOIN teams th ON th.id=m.home_team_id
        LEFT JOIN teams ta ON ta.id=m.away_team_id
        WHERE m.kickoff_time >= '2026-08-15 12:00:00' AND m.kickoff_time < '2026-08-17 12:00:00'
          AND m.id IN (SELECT mm.id FROM matches mm
                       LEFT JOIN teams hh ON hh.id=mm.home_team_id
                       LEFT JOIN teams aa ON aa.id=mm.away_team_id
                       WHERE mm.kickoff_time >= '2026-08-15 12:00:00' AND mm.kickoff_time < '2026-08-17 12:00:00'
                         AND NOT EXISTS (
                           SELECT 1 FROM head_to_head h2
                           WHERE ((h2.home_team_id=hh.id AND h2.away_team_id=aa.id)
                              OR (h2.home_team_id=aa.id AND h2.away_team_id=hh.id))
                         ))
        ORDER BY m.kickoff_time
        """
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_num']} [{r['name_zh']}] {r['hzh']}(sm={r['hsm']}) vs {r['azh']}(sm={r['asm']}) ko={r['kickoff_time']}")

    await conn.close()


asyncio.run(main())
