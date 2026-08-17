"""查看最新 match_fixtures 日志 + 未匹配比赛详情"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """
        SELECT task_type, status, start_time, duration_ms, message
        FROM task_logs
        WHERE task_type IN ('match_fixtures','sync_odds')
        ORDER BY start_time DESC LIMIT 6
        """
    )
    for r in rows:
        print(f"### {r['start_time']} {r['task_type']} {r['status']} ({r['duration_ms']}ms)")
        print(f"    {r['message']}")
        print()

    print("=== 未匹配 3 场球队详情 ===")
    rows2 = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.home_team_name AS hraw, m.away_team_name AS araw,
               th.id AS hid, th.name_zh AS hzh, th.name_en AS hen, th.sportmonks_id AS hsm,
               ta.id AS aid, ta.name_zh AS azh, ta.name_en AS aen, ta.sportmonks_id AS asm
        FROM matches m
        LEFT JOIN teams th ON th.id = m.home_team_id
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.id IN (15622, 15627, 15633)
        """
    )
    for r in rows2:
        print(f"  #{r['id']} {r['match_num']}: {r['hraw']}({r['hid']},zh={r['hzh']},en={r['hen']},sm={r['hsm']}) vs {r['araw']}({r['aid']},zh={r['azh']},en={r['aen']},sm={r['asm']})")

    await conn.close()


asyncio.run(main())
