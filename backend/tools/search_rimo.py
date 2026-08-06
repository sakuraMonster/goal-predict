import asyncio, sys
from dotenv import load_dotenv
load_dotenv()
from app.collector.sportmonks.client import SportMonksClient

async def main():
    sm = SportMonksClient()
    
    # 里莫的对手是米拉索尔 (sm_id=11126)，客队
    # 先查日期
    print("1. 查询 07-30 fixtures...", flush=True)
    try:
        fixtures_data = await sm._get("/fixtures/date/2026-07-30", {"include": "participants"})
        fixtures = fixtures_data.get("data", [])
        print(f"  共 {len(fixtures)} 场", flush=True)
        for f in fixtures:
            parts = f.get("participants", [])
            ids = [p.get('id') for p in parts]
            if 11126 in ids:
                print(f"  找到: fixture={f.get('id')} {f.get('name')}", flush=True)
                for p in parts:
                    print(f"    id={p.get('id')} name={p.get('name')} short={p.get('short_code')}", flush=True)
    except Exception as e:
        print(f"  失败: {e}", flush=True)

    # 搜索可能的队名
    print("\n2. 搜索 Remo...", flush=True)
    try:
        results = await sm.search_teams("Remo")
        print(f"  共 {len(results)} 结果", flush=True)
        for r in results[:5]:
            print(f"  id={r.get('id')} name={r.get('name')} short={r.get('short_code')} country={r.get('country')}", flush=True)
    except Exception as e:
        print(f"  失败: {e}", flush=True)

    await sm.close()

asyncio.run(main())
