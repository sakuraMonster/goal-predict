"""重新匹配 fixture + 同步赔率"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from app.collector.pipeline import SyncPipeline


async def main():
    pipeline = SyncPipeline()

    # Step 1: 重新匹配 fixture
    print("="*60)
    print("Step 1: match_to_sportmonks")
    print("="*60)
    await pipeline.match_to_sportmonks()

    # Step 2: 同步赔率
    print("\n" + "="*60)
    print("Step 2: sync_odds")
    print("="*60)
    await pipeline.sync_odds()

    print("\n完成!")


if __name__ == "__main__":
    asyncio.run(main())
