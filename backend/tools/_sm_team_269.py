"""调试 SM 球队 269 返回结构"""
import asyncio
import json
import os
import sys
from dotenv import load_dotenv
load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collector.sportmonks.client import SportMonksClient


async def main():
    sm = SportMonksClient()
    data = await sm._get(f"/teams/{269}", {})
    print(json.dumps(data, ensure_ascii=False, indent=1)[:1500])
    await sm.close()


asyncio.run(main())
