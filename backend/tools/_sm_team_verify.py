"""验证 SM 球队 269 / 1608 身份 + 11914 近期状态
"""
import asyncio
import os
import sys
from dotenv import load_dotenv
load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collector.sportmonks.client import SportMonksClient


async def main():
    sm = SportMonksClient()
    for smid in [269, 11914, 5402]:
        try:
            data = await sm.get_team_by_id(smid, includes="")
            t = data.get("data") if isinstance(data, dict) and "data" in data else data
            if isinstance(t, dict):
                print(f"  sm={smid}: name={t.get('name')} short_code={t.get('short_code')} country={t.get('country')} image={t.get('image_path')}")
            else:
                print(f"  sm={smid}: raw={str(t)[:200]}")
        except Exception as e:
            print(f"  sm={smid}: 异常 {type(e).__name__}: {e}")
    await sm.close()


asyncio.run(main())
