"""t2 重采集：触发 sync_team_info（24h 内比赛球队拉取真实 TeamSeasonStats）"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.collector.pipeline import SyncPipeline


async def main():
    p = SyncPipeline()
    await p.sync_team_info()
    await p.sm.close()


asyncio.run(main())
