import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect("postgresql://postgres:postgres@localhost/football_prediction")
    r = await conn.fetch("SELECT count(*) FROM odds_snapshots")
    r2 = await conn.fetch("SELECT count(*) FROM task_logs WHERE task_type=$1", "sync_odds")
    r3 = await conn.fetch("SELECT task_type, status, message FROM task_logs ORDER BY created_at DESC LIMIT 5")
    print(f"odds_snapshots count: {r[0][0]}")
    print(f"sync_odds logs: {r2[0][0]}")
    for log in r3:
        print(f"  [{log['task_type']}] {log['status']}: {log['message'][:100]}")
    await conn.close()
asyncio.run(check())
