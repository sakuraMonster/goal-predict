"""搜索 SM 利勒斯特罗姆 (Lillestrøm) 正确 ID"""
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
    # 搜索 Lillestrøm / Lillestrom
    for q in ["Lillestrøm", "Lillestrom", "Lilleström"]:
        try:
            data = await sm._get("/teams/search/Lillestrøm", {})
            items = data.get("data", [])
            print(f"  q=Lillestrøm: {len(items)} 条")
            for it in items[:10]:
                print(f"    id={it.get('id')} name={it.get('name')} code={it.get('short_code')} country_id={it.get('country_id')} placeholder={it.get('placeholder')}")
        except Exception as e:
            print(f"  搜索异常 {q}: {e}")
        await asyncio.sleep(2)
    await sm.close()


asyncio.run(main())
