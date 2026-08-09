"""检查API返回的08-07比赛数据和日期列表"""
import asyncio
import httpx

async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        # 查比赛
        resp = await client.get("http://localhost:8008/api/matches?date=2026-08-07")
        data = resp.json().get("data", [])
        print(f"=== /api/matches?date=2026-08-07: {len(data)} matches ===")
        for m in data:
            print(f"  {m.get('match_num','')} {m['home_team']} vs {m['away_team']} ko={m.get('kickoff_time','')}")

        print()

        # 查可用日期
        resp2 = await client.get("http://localhost:8008/api/matches/dates")
        dates = resp2.json().get("data", [])
        print(f"=== /api/matches/dates ===")
        for d in dates:
            print(f"  date={d.get('date')} is_past={d.get('is_past')} count={d.get('count')}")

if __name__ == "__main__":
    asyncio.run(main())
