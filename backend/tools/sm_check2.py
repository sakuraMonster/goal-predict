"""快速查询 SM 联赛和 fixture 信息"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.collector.sportmonks.client import SportMonksClient

async def main():
    client = SportMonksClient()
    try:
        # 查巴西联赛和欧冠相关
        leagues = await client.get_all_leagues()
        for keyword in ["brasileiro", "champions league", "championship"]:
            matched = [l for l in leagues if keyword in l.get("name", "").lower()]
            print(f"\n=== '{keyword}' ({len(matched)}) ===")
            for l in matched[:5]:
                print(f"  id={l['id']}, name={l['name']}, country={l.get('country',{}).get('name','?')}")
        
        # 查 fixtures 19766313, 19766308, 19710148, 19710145 的联赛信息
        print("\n=== 已匹配的 SM fixture 联赛确认 ===")
        for fid in [19766313, 19766308, 19710148, 19710145]:
            f = await client.get_fixture_by_id(fid, includes="league;participants")
            league = f.get("league", {})
            participants = f.get("participants", [])
            pnames = " vs ".join(p.get("name", "?") for p in participants[:2])
            print(f"  fixture={fid}: league={league.get('name')}(id={league.get('id')}), {pnames}")
        
        # 查 08-06 所有 fixtures (不限联赛)
        print("\n=== 08-06 欧冠相关 fixtures ===")
        fixtures = await client.get_fixtures_by_date("2026-08-06", includes="league;participants")
        ucl = [f for f in fixtures if f.get("league",{}).get("id") == 2]
        for f in ucl[:10]:
            league = f.get("league", {})
            participants = f.get("participants", [])
            pnames = " vs ".join(p.get("name", "?") for p in participants[:2])
            print(f"  fixture={f['id']}, {pnames}")
            
    finally:
        await client.close()

asyncio.run(main())
