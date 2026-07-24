"""
APScheduler 定时任务配置（Redis 分布式锁防重复执行）
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.collector.pipeline import SyncPipeline
from app.db.redis_client import acquire_lock, release_lock

scheduler = AsyncIOScheduler()


async def _run_with_lock(task_name: str, fn):
    """带 Redis 分布式锁的任务执行包装"""
    lock_key = f"task_lock:{task_name}"
    acquired = await acquire_lock(lock_key, expire_seconds=600)
    if not acquired:
        print(f"[{task_name}] 已有实例在运行，跳过本次执行")
        return
    try:
        await fn()
    finally:
        await release_lock(lock_key)


async def _run_sync_matches():
    pipeline = SyncPipeline()
    await _run_with_lock("sync_matches", pipeline.sync_daily_matches)


async def _run_sync_odds():
    pipeline = SyncPipeline()
    await _run_with_lock("sync_odds", pipeline.sync_odds)


async def _run_update_teams():
    pipeline = SyncPipeline()
    await _run_with_lock("update_teams", pipeline.sync_team_info)


def init_scheduler():
    """初始化并启动所有定时任务"""
    scheduler.add_job(_run_sync_matches, CronTrigger(hour=9, minute=0), id="sync_matches_09")
    scheduler.add_job(_run_sync_matches, CronTrigger(hour=12, minute=0), id="sync_matches_12")

    for hour in [9, 14, 18]:
        scheduler.add_job(_run_sync_odds, CronTrigger(hour=hour, minute=0), id=f"sync_odds_{hour}")

    scheduler.add_job(_run_update_teams, CronTrigger(hour=3, minute=0), id="update_teams")

    scheduler.start()
