"""直接调用 /fixtures/19720989?include=trends"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv; load_dotenv()
from app.collector.sportmonks.client import SportMonksClient

async def main():
    client = SportMonksClient()
    try:
        data = await client.get_fixture_by_id(19720989, includes="trends")
        # 检查 trends 结构
        trends = data.get("trends", [])
        print(f"trends count: {len(trends)}")
        if trends:
            # 看有哪些 type_id
            type_ids = set()
            for t in trends[:20]:
                type_ids.add(t.get("type_id"))
                print(f"  type_id={t.get('type_id')} pid={t.get('participant_id')} val={t.get('value')} min={t.get('minute')}")
            print(f"type_ids: {type_ids}")
            # 查 117 是否存在
            xg_trends = [t for t in trends if t.get("type_id") == 117]
            print(f"xG(117) trends: {len(xg_trends)}")
        else:
            print("NO TRENDS RETURNED")
            # 打印顶层keys
            print(f"top keys: {list(data.keys())[:10]}")
            # 检查是否有其他趋势字段
            for k in data.keys():
                if 'trend' in k.lower() or 'xg' in k.lower() or 'stat' in k.lower():
                    v = data.get(k)
                    print(f"  {k}: {type(v).__name__} = {str(v)[:200]}")
    finally:
        await client.close()

asyncio.run(main())
