"""
APScheduler 定时任务配置
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.collector.pipeline import SyncPipeline

scheduler = AsyncIOScheduler()


async def _run_sync_matches():
    pipeline = SyncPipeline()
    await pipeline.sync_daily_matches()


async def _run_sync_odds():
    pipeline = SyncPipeline()
    await pipeline.sync_odds()


async def _run_update_teams():
    pipeline = SyncPipeline()
    await pipeline.sync_team_info()


def init_scheduler():
    """初始化并启动所有定时任务"""
    # 赛程同步：每日 09:00, 12:00
    scheduler.add_job(_run_sync_matches, CronTrigger(hour=9, minute=0), id="sync_matches_09")
    scheduler.add_job(_run_sync_matches, CronTrigger(hour=12, minute=0), id="sync_matches_12")

    # 赔率更新：每日 09:00, 14:00, 18:00
    for hour in [9, 14, 18]:
        scheduler.add_job(_run_sync_odds, CronTrigger(hour=hour, minute=0), id=f"sync_odds_{hour}")

    # 球队信息：每日 03:00
    scheduler.add_job(_run_update_teams, CronTrigger(hour=3, minute=0), id="update_teams")

    scheduler.start()
