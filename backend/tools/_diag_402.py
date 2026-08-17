"""查 402 h2h 引用 + 1202 现状 + 最新 task_logs"""
import asyncio
import asyncpg
import json

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    out = []

    t = await conn.fetchrow(
        "SELECT id, name_zh, name_en, sportmonks_id, league_id, "
        "(SELECT count(*) FROM matches WHERE home_team_id=teams.id) AS h, "
        "(SELECT count(*) FROM matches WHERE away_team_id=teams.id) AS a "
        "FROM teams WHERE id=1202")
    out.append("1202=" + json.dumps(dict(t), ensure_ascii=True))

    t = await conn.fetchrow("SELECT count(*) AS c FROM head_to_head WHERE home_team_id=402 OR away_team_id=402")
    out.append("402_h2h_count=" + str(t["c"]))

    t = await conn.fetchrow(
        "SELECT (SELECT count(*) FROM matches WHERE home_team_id=402) AS h, "
        "(SELECT count(*) FROM matches WHERE away_team_id=402) AS a")
    out.append("402_match_ref=" + json.dumps(dict(t)))

    rows = await conn.fetch(
        "SELECT task_type, status, start_time, duration_ms, left(message,160) AS msg "
        "FROM task_logs WHERE task_type IN ('match_fixtures','sync_odds') "
        "AND start_time >= '2026-08-15 01:20:00' ORDER BY start_time DESC LIMIT 8")
    out.append("recent_tasks=" + json.dumps([dict(r, start_time=str(r["start_time"])) for r in rows], ensure_ascii=True))

    open("_out402.txt", "w", encoding="utf-8").write("\n".join(out))
    await conn.close()


asyncio.run(main())
