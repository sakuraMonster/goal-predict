"""通过 SM API 查找欧罗巴相关的联赛"""
import asyncio, os
from dotenv import load_dotenv
load_dotenv()
from app.collector.sportmonks.client import SportMonksClient

async def main():
    client = SportMonksClient()
    try:
        # 查所有联赛，筛选欧罗巴相关
        print("=== 查询 SM 联赛列表 ===")
        leagues = await client.get_all_leagues()
        europa = [l for l in leagues if l.get("name", "").lower().find("europa") >= 0]
        ucl = [l for l in leagues if l.get("name", "").lower().find("champions") >= 0]
        uecl = [l for l in leagues if l.get("name", "").lower().find("conference") >= 0]
        
        print(f"\n欧罗巴相关 ({len(europa)}):")
        for l in europa:
            print(f"  id={l['id']}, name={l['name']}, country={l.get('country',{}).get('name','?')}")
        
        print(f"\n欧冠相关 ({len(ucl)}):")
        for l in ucl[:5]:
            print(f"  id={l['id']}, name={l['name']}")
        
        print(f"\n欧协联相关 ({len(uecl)}):")
        for l in uecl:
            print(f"  id={l['id']}, name={l['name']}")
            
        # 查 08-06 的 fixtures 来找 Europa League 的
        print("\n=== 08-06 SM fixtures (含欧罗巴) ===")
        fixtures = await client.get_fixtures_by_date("2026-08-06", includes="league;participants")
        for f in fixtures:
            league = f.get("league", {})
            lname = league.get("name", "?")
            if "europa" in lname.lower() or "champions" in lname.lower() or "conference" in lname.lower():
                participants = f.get("participants", [])
                pnames = " vs ".join(p.get("name", "?") for p in participants[:2])
                print(f"  fixture={f['id']}, league={lname}({league.get('id')}), {pnames}")
        
        # 查 08-07
        print("\n=== 08-07 SM fixtures (含欧罗巴/欧冠) ===")
        fixtures2 = await client.get_fixtures_by_date("2026-08-07", includes="league;participants")
        for f in fixtures2:
            league = f.get("league", {})
            lname = league.get("name", "?")
            if "europa" in lname.lower() or "champions" in lname.lower() or "conference" in lname.lower():
                participants = f.get("participants", [])
                pnames = " vs ".join(p.get("name", "?") for p in participants[:2])
                print(f"  fixture={f['id']}, league={lname}({league.get('id')}), {pnames}")
                
    finally:
        await client.close()

asyncio.run(main())
