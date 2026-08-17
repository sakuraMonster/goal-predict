"""验证 15627 柏林赫塔(3317) vs 海登海姆(2831) 的 SM H2H 是否存在（不同 include/无 limit）"""
import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv(r"e:\zhangxuejun\new-thinking\ricking-03\backend\.env", override=True)
OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_h2h_3317.txt"


async def main():
    key = os.getenv("SPORTMONKS_API_KEY")
    lines = []

    def p(s=""):
        lines.append(str(s))
        print(s)

    async with httpx.AsyncClient(timeout=30) as client:
        # 带 include 的 H2H
        resp = await client.get(
            "https://api.sportmonks.com/v3/football/fixtures/head-to-head/3317/2831",
            params={"api_token": key, "include": "participants;scores", "per_page": 10},
        )
        d = resp.json().get("data", [])
        p(f"=== SM H2H 3317(Hertha) vs 2831(Heidenheim) include: {len(d)} 条 ===")
        for f in d[:8]:
            p(f"  fx={f['id']} {f.get('starting_at')} {f.get('name')}")

        # 不带 include 试试
        resp2 = await client.get(
            "https://api.sportmonks.com/v3/football/fixtures/head-to-head/3317/2831",
            params={"api_token": key, "per_page": 10},
        )
        d2 = resp2.json().get("data", [])
        p(f"\n=== SM H2H 无 include: {len(d2)} 条 ===")
        for f in d2[:8]:
            p(f"  fx={f['id']} {f.get('starting_at')} {f.get('name')}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    p(f"\n[written] {OUT}")


asyncio.run(main())
