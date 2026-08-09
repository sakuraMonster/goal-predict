"""检查各联赛在 SM 中的赛季列表"""
import asyncio, sys
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from app.collector.sportmonks.client import SportMonksClient
from dotenv import load_dotenv
import os
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(env_path)

TARGETS = [
    ("葡超", 462), ("英冠", 9), ("荷乙", 74), ("德乙", 85),
]

async def main():
    sm = SportMonksClient()
    for name, lid in TARGETS:
        lg = await sm.get_league_by_id(lid)
        seasons = lg.get("seasons", [])
        print(f"\n{name} (id={lid}):")
        for s in seasons:
            print(f"  id={s['id']}  name={s.get('name','?')}  from={s.get('starting_at','')[:10]}  to={s.get('ending_at','')[:10]}  current={s.get('is_current')}  finished={s.get('finished')}")
    await sm.close()

if __name__ == "__main__":
    asyncio.run(main())
