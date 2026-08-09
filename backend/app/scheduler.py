"""
APScheduler 定时任务配置（Redis 分布式锁防重复执行）
"""
import traceback
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.collector.pipeline import SyncPipeline
from app.db.redis_client import acquire_lock, release_lock

# 让 APScheduler 的日志输出到控制台
logging.getLogger("apscheduler").setLevel(logging.WARNING)

scheduler = AsyncIOScheduler()


async def _run_with_lock(task_name: str, fn):
    """带 Redis 分布式锁的任务执行包装"""
    lock_key = f"task_lock:{task_name}"
    try:
        acquired = await acquire_lock(lock_key, expire_seconds=600)
    except Exception as e:
        print(f"[SCHEDULER] [{task_name}] Redis 锁获取失败: {e}", flush=True)
        return
    if not acquired:
        print(f"[SCHEDULER] [{task_name}] 已有实例在运行，跳过本次执行", flush=True)
        return
    try:
        await fn()
    except Exception as e:
        print(f"[SCHEDULER] [{task_name}] 执行异常: {e}", flush=True)
        traceback.print_exc()
    finally:
        try:
            await release_lock(lock_key)
        except Exception:
            pass


async def _run_sync_matches():
    print(f"[SCHEDULER] sync_matches 触发", flush=True)
    pipeline = SyncPipeline()
    await _run_with_lock("sync_matches", pipeline.sync_daily_matches)


async def _run_sync_odds():
    print(f"[SCHEDULER] sync_odds 触发", flush=True)
    pipeline = SyncPipeline()
    await _run_with_lock("sync_odds", pipeline.sync_odds)


async def _run_update_teams():
    print(f"[SCHEDULER] update_teams 触发", flush=True)
    pipeline = SyncPipeline()
    await _run_with_lock("update_teams", pipeline.sync_team_info)


def init_scheduler():
    """初始化并启动所有定时任务（在 FastAPI startup 事件中调用，确保事件循环已就绪）"""
    from pytz import timezone
    tz = timezone("Asia/Shanghai")

    scheduler = AsyncIOScheduler(timezone=tz)

    scheduler.add_job(_run_sync_matches, CronTrigger(hour=9, minute=0, timezone=tz), id="sync_matches_09")
    scheduler.add_job(_run_sync_matches, CronTrigger(hour=12, minute=0, timezone=tz), id="sync_matches_12")

    scheduler.add_job(_run_sync_odds, CronTrigger(minute="*/30"), id="sync_odds_30m")

    scheduler.add_job(_run_update_teams, CronTrigger(hour=3, minute=0, timezone=tz), id="update_teams")

    scheduler.start()
    print("[SCHEDULER] 定时任务调度器已启动，jobs:", [j.id for j in scheduler.get_jobs()], flush=True)
