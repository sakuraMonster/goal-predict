"""task_logs 中 sync_odds / match_fixtures / sync_matches 历史"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """
        SELECT task_type, status, start_time, duration_ms, left(message,150) AS msg
        FROM task_logs
        WHERE task_type IN ('sync_odds','match_fixtures','sync_matches')
        ORDER BY start_time DESC LIMIT 25
        """
    )
    for r in rows:
        print(f"  {r['start_time']} {r['task_type']:<14} {r['status']:<8} {r['duration_ms']}ms | {r['msg']}")
    await conn.close()


asyncio.run(main())
