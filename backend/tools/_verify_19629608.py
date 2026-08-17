"""SM 校验 19629608 方向 + 查 173 球队 + 1590 近期状态缺口"""
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv(r"e:\zhangxuejun\new-thinking\ricking-03\backend\.env", override=True)

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_19629608.txt"

import httpx


async def main():
    conn = await asyncpg.connect(DSN)
    lines = []

    def p(s=""):
        lines.append(str(s))
        print(s)

    # 1. 查 173 球队
    p("=== teams 173 ===")
    r = await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE id=173")
    p(f"  173: zh={r['name_zh']!r} en={r['name_en']!r} sm={r['sportmonks_id']}")

    # 2. SM fixture 19629608 方向
    key = os.getenv("SPORTMONKS_API_KEY")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://api.sportmonks.com/v3/football/fixtures/19629608",
            params={"api_token": key, "include": "participants"},
        )
        d = resp.json().get("data", {})
        p(f"\n=== SM fixture 19629608 ===")
        p(f"  starting_at={d.get('starting_at')} name={d.get('name')!r}")
        parts = d.get("participants")
        if isinstance(parts, dict):
            parts = parts.get("data", [])
        for part in parts or []:
            p(f"  {part.get('meta', {}).get('position')}: id={part.get('id')} name={part.get('name')!r}")

    # 3. 1590 是否缺 season=latest stats
    p("\n=== 1590 stats (所有 season) ===")
    rows = await conn.fetch("SELECT id, season, league_id, played, form FROM team_season_stats WHERE team_id=1590")
    for r in rows:
        p(f"  #{r['id']} season={r['season']} lg={r['league_id']} played={r['played']} form={r['form']}")

    # 4. SM team 2510 latest 最近几场
    resp2 = await client.get(
        "https://api.sportmonks.com/v3/football/teams/2510",
        params={"api_token": key, "include": "latest"},
    )
    latest = resp2.json().get("data", {}).get("latest", {}).get("data", [])
    p(f"\n=== SM team 2510 latest 条数={len(latest)} ===")
    for f in latest[:6]:
        p(f"  fx={f['id']} {f.get('starting_at')} {f.get('name')}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    p(f"\n[written] {OUT}")
    await conn.close()


asyncio.run(main())
