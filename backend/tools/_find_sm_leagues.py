"""查找荷乙、德乙的正确 SM league ID"""
import asyncio, sys
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from app.collector.sportmonks.client import SportMonksClient
from dotenv import load_dotenv
import os
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(env_path)

async def main():
    sm = SportMonksClient()

    # 方式1: 搜索关键词
    print("=== 搜索 'Eerste Divisie' (荷乙) ===")
    leagues = await sm.get_all_leagues()
    for lg in leagues:
        name = lg.get("name", "")
        if "eerste" in name.lower() or "divisie" in name.lower():
            print(f"  id={lg['id']}  name={name}  country={lg.get('country',{}).get('name','')}  active={lg.get('active')}")

    print()
    print("=== 搜索 'Bundesliga' (德乙) ===")
    for lg in leagues:
        name = lg.get("name", "")
        if "bundesliga" in name.lower():
            print(f"  id={lg['id']}  name={name}  country={lg.get('country',{}).get('name','')}  active={lg.get('active')}")

    print()
    print("=== 搜索 'Championship' (英冠, 确认ID) ===")
    for lg in leagues:
        name = lg.get("name", "")
        if "championship" in name.lower() and "england" in str(lg.get("country",{}).get("name","")).lower():
            print(f"  id={lg['id']}  name={name}")

    # 方式2: 直接试常见 ID
    print()
    print("=== 直接测试候选 ID ===")
    candidates = {
        "荷乙": [36, 37, 72, 435, 434, 433],
        "德乙": [239, 246, 245, 244],
    }
    for label, ids in candidates.items():
        for lid in ids:
            try:
                lg = await sm.get_league_by_id(lid)
                name = lg.get("name", "N/A")
                seasons = lg.get("seasons", [])
                finished = [s for s in seasons if s.get("finished")]
                print(f"  {label} id={lid}: name='{name}' seasons={len(seasons)} finished={len(finished)}")
            except Exception as e:
                pass  # not found or error

    await sm.close()

if __name__ == "__main__":
    asyncio.run(main())
