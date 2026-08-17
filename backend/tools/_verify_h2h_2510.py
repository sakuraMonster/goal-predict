"""验证 11914 vs 2510 的 SM H2H + 检查 scheduler/update_teams 状态"""
import asyncio
import asyncpg
import os
import httpx
from dotenv import load_dotenv

load_dotenv(r"e:\zhangxuejun\new-thinking\ricking-03\backend\.env", override=True)
DSN = "postgresql://postgres:postgres@localhost/football_prediction"
OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_h2h_2510.txt"


async def main():
    conn = await asyncpg.connect(DSN)
    lines = []

    def p(s=""):
        lines.append(str(s))
        print(s)

    key = os.getenv("SPORTMONKS_API_KEY")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://api.sportmonks.com/v3/football/fixtures/head-to-head/11914/2510",
            params={"api_token": key, "include": "participants;scores"},
        )
        d = resp.json().get("data", [])
        p(f"=== SM H2H 11914(KFUM) vs 2510(Lillestrøm): {len(d)} 条 ===")
        for f in d[:6]:
            p(f"  fx={f['id']} {f.get('starting_at')} {f.get('name')}")

    p("\n=== 最近 task_logs (48h) ===")
    rows = await conn.fetch(
        """SELECT task_type, status, start_time, duration_ms, message FROM task_logs
           WHERE start_time >= now() - interval '48 hours' ORDER BY start_time DESC LIMIT 30"""
    )
    for r in rows:
        p(f"  {r['start_time']} {r['task_type']:15s} {r['status']:8s} {r['duration_ms']}ms {r['message']}")

    p("\n=== task_logs 中 update_teams 最后 5 条 ===")
    rows = await conn.fetch(
        """SELECT task_type, status, start_time, duration_ms, message FROM task_logs
           WHERE task_type='update_teams' ORDER BY start_time DESC LIMIT 5"""
    )
    for r in rows:
        p(f"  {r['start_time']} {r['status']:8s} {r['duration_ms']}ms {r['message']}")

    p("\n=== scheduler 状态（查 uvicorn 进程？改查 api_request 最近 update-teams）===")
    rows = await conn.fetch(
        """SELECT start_time, message FROM task_logs
           WHERE task_type='api_request' AND message ILIKE '%update-teams%'
           ORDER BY start_time DESC LIMIT 5"""
    )
    for r in rows:
        p(f"  {r['start_time']} {r['message']}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    p(f"\n[written] {OUT}")
    await conn.close()


asyncio.run(main())
