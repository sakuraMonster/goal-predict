"""验证周日比赛修复 + 最新 sync_odds 日志"""
import asyncio
import asyncpg
import json

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    out = []

    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, l.name_zh AS lg, m.kickoff_time, m.sportmonks_fixture_id AS fx, m.is_swapped,
               th.name_zh AS hzh, ta.name_zh AS azh,
               th.sportmonks_id AS hsm, ta.sportmonks_id AS asm,
               (SELECT count(*) FROM odds_snapshots o WHERE o.match_id=m.id) AS odds_cnt
        FROM matches m
        LEFT JOIN leagues l ON l.id = m.league_id
        LEFT JOIN teams th ON th.id = m.home_team_id
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.kickoff_time >= '2026-08-16 12:00:00' AND m.kickoff_time < '2026-08-17 12:00:00'
        ORDER BY m.kickoff_time
        """
    )
    out.append("== 周日比赛 ==")
    for r in rows:
        out.append(json.dumps(dict(r, kickoff_time=str(r["kickoff_time"])), ensure_ascii=True))

    rows = await conn.fetch(
        "SELECT task_type, status, start_time, duration_ms, message FROM task_logs "
        "WHERE task_type='sync_odds' ORDER BY start_time DESC LIMIT 2")
    out.append("== 最新 sync_odds ==")
    for r in rows:
        out.append(json.dumps(dict(r, start_time=str(r["start_time"])), ensure_ascii=True))

    open("_out_final.txt", "w", encoding="utf-8").write("\n".join(out))
    await conn.close()


asyncio.run(main())
