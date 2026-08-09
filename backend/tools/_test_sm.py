"""测试 SM API 连通性"""
import asyncio, sys
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from app.collector.sportmonks.client import SportMonksClient
from dotenv import load_dotenv
import os
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

async def test():
    sm = SportMonksClient()
    try:
        print("Test 1: get_league_by_id(462)...")
        lg = await asyncio.wait_for(sm.get_league_by_id(462), timeout=10)
        print(f"  OK: {lg.get('name')}")

        print("Test 2: fixtures/between 2025-08-08 ~ 2025-08-11...")
        fx = await asyncio.wait_for(
            sm.get_fixtures_between("2025-08-08", "2025-08-11", includes="scores"),
            timeout=20
        )
        print(f"  OK: {len(fx)} fixtures")

        print("Test 3: same but with participants...")
        fx2 = await asyncio.wait_for(
            sm.get_fixtures_between("2025-08-08", "2025-08-11", includes="scores;participants"),
            timeout=20
        )
        print(f"  OK: {len(fx2)} fixtures")
    except asyncio.TimeoutError:
        print("  TIMEOUT!")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
    await sm.close()

asyncio.run(test())
