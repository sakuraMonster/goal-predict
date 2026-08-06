import asyncio
from app.collector.pipeline import SyncPipeline

async def main():
    pipeline = SyncPipeline()
    print("Syncing H2H for 周五002 (Bodø/Glimt vs Lillestrøm)...")
    count = await pipeline._sync_head_to_head_for_pair(170, 1590)
    print(f"H2H synced: {count} records")
    await pipeline.close()

asyncio.run(main())
