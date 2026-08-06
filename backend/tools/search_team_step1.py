"""Step 1: 搜索波兹南莱赫"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.collector.sportmonks.client import SportMonksClient

async def main():
    sm = SportMonksClient()
    print("=== 波兹南莱赫 ===", flush=True)
    
    # 通过 fixture 找参赛队
    print("查询 Fixture 19721239...", flush=True)
    fx = await sm.get_fixture_by_id(19721239, includes="participants")
    participants = fx.get("participants", [])
    print(f"参赛队: {len(participants)}", flush=True)
    for p in participants:
        print(f"  id={p.get('id')} name={p.get('name')} short={p.get('short_code')}", flush=True)
    
    await sm.close()

asyncio.run(main())
