"""调试 SM fixture 数据结构"""
import asyncio, sys, json
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from app.collector.sportmonks.client import SportMonksClient
from dotenv import load_dotenv
import os
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(env_path)

async def main():
    sm = SportMonksClient()
    
    # 拿葡超一个短日期范围的 fixture  
    fixtures = await sm.get_fixtures_between("2024-10-01", "2024-10-03", includes="participants;scores;league")
    print(f"Returned {len(fixtures)} fixtures")
    
    if fixtures:
        fx = fixtures[0]
        print(f"\n=== 第一条 fixture keys ===")
        print(list(fx.keys()))
        
        # Check league
        league = fx.get("league", {})
        print(f"\nleague: {json.dumps(league, ensure_ascii=False)[:200]}")
        
        # Check state
        state = fx.get("state", {})
        print(f"\nstate: {json.dumps(state, ensure_ascii=False)}")
        
        # Check scores
        scores = fx.get("scores", [])
        print(f"\nscores ({len(scores)}):")
        for s in scores:
            print(f"  {json.dumps(s, ensure_ascii=False)[:200]}")
        
        # Check participants
        parts = fx.get("participants", [])
        print(f"\nparticipants ({len(parts)}):")
        for p in parts:
            print(f"  {json.dumps(p, ensure_ascii=False)[:300]}")

    # 找葡超 (league.id=462)
    pg_fx = [f for f in fixtures if f.get("league", {}).get("id") == 462]
    print(f"\n\n葡超 fixtures: {len(pg_fx)}")
    if pg_fx:
        fx = pg_fx[0]
        print(f"\n=== 葡超 fixture ===")
        # state
        s = fx.get("state", {}).get("state", "")
        print(f"state: {s}")
        # scores
        for sc in fx.get("scores", []):
            print(f"score: desc={sc.get('description')} score={sc.get('score')}")
        # name
        print(f"name: {fx.get('name')}")
        # starting_at
        print(f"starting_at: {fx.get('starting_at')}")
    
    await sm.close()

asyncio.run(main())
