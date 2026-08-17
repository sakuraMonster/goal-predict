"""调用完整 sync_odds，让多线 OU 数据入库（验证 OU_MULTI_LINE_STORAGE=True 路径）"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app.collector.pipeline import SyncPipeline

async def main():
    p = SyncPipeline()
    await p.sync_odds()
    print("SYNC_ODDS DONE")

asyncio.run(main())
