"""为特定球队重新拉取 SportMonks 数据"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()
from app.collector.pipeline import SyncPipeline

async def main():
    p = SyncPipeline()
    print("重新拉取球队数据...")
    await p.sync_team_info()
    print("完成")

asyncio.run(main())
