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
    """带 Redis 分布式锁的任务执行包装，Redis 不可达时降级为无锁执行"""
    lock_key = f"task_lock:{task_name}"
    acquired = None  # None=Redis异常, False=锁被占用, True=获取成功
    try:
        acquired = await acquire_lock(lock_key, expire_seconds=600)
    except Exception as e:
        print(f"[SCHEDULER] [{task_name}] Redis 不可达，降级为无锁执行: {e}", flush=True)

    if acquired is False:
        print(f"[SCHEDULER] [{task_name}] 已有实例在运行，跳过本次执行", flush=True)
        return

    try:
        await fn()
    except Exception as e:
        print(f"[SCHEDULER] [{task_name}] 执行异常: {e}", flush=True)
        traceback.print_exc()
    finally:
        if acquired is True:
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


async def _run_sync_standings():
    print(f"[SCHEDULER] sync_standings 触发", flush=True)
    pipeline = SyncPipeline()
    await _run_with_lock("sync_standings", pipeline.sync_standings)


async def _run_update_results():
    """每日结算昨日比赛日赛果：抓取竞彩网 → 回写 Prediction actual/result + 同步 Match status/比分"""
    print(f"[SCHEDULER] update_results 触发", flush=True)
    from app.api import reports
    from app.db.database import async_session

    # 默认结算昨日竞彩比赛日范围 [昨日12:00, 今日12:00)，与端点 update_results 无参行为一致
    start, end, date_str = reports._date_range()
    start_date = start.strftime("%Y-%m-%d")
    end_date = end.strftime("%Y-%m-%d")

    try:
        results = await reports._scrape_sporttery_results(start_date, end_date)
    except Exception as e:
        print(f"[SCHEDULER] update_results 赛果抓取失败: {e}", flush=True)
        traceback.print_exc()
        return

    if not results:
        print(f"[SCHEDULER] update_results 未抓取到赛果（比赛日 {date_str}）", flush=True)
        return

    async with async_session() as db:
        try:
            updated, _ = await reports._match_and_update(db, results)
        except Exception as e:
            print(f"[SCHEDULER] update_results 赛果匹配更新失败: {e}", flush=True)
            traceback.print_exc()
            return

    print(f"[SCHEDULER] update_results 完成: 比赛日 {date_str}，抓取 {len(results)} 条，更新 {updated} 场", flush=True)


def init_scheduler():
    """初始化并启动所有定时任务（在 FastAPI startup 事件中调用，确保事件循环已就绪）"""
    from pytz import timezone
    tz = timezone("Asia/Shanghai")

    scheduler = AsyncIOScheduler(timezone=tz)

    scheduler.add_job(_run_sync_matches, CronTrigger(hour=9, minute=0, timezone=tz), id="sync_matches_09")
    scheduler.add_job(_run_sync_matches, CronTrigger(hour=12, minute=0, timezone=tz), id="sync_matches_12")

    scheduler.add_job(_run_sync_odds, CronTrigger(minute="*/30"), id="sync_odds_30m")

    scheduler.add_job(_run_update_teams, CronTrigger(hour=3, minute=0, timezone=tz), id="update_teams")

    # 联赛积分榜排名每日同步（冷门优选依赖排名冲突信号，需在比赛日 12:00 前更新）
    scheduler.add_job(_run_sync_standings, CronTrigger(hour=8, minute=0, timezone=tz), id="sync_standings_08")

    # 每日赛果结算：昨日比赛日 [昨日12:00, 今日12:00) 已完场 → 回写结果并同步 Match status=finished
    scheduler.add_job(_run_update_results, CronTrigger(hour=12, minute=0, timezone=tz), id="update_results_1200")

    scheduler.start()
    print("[SCHEDULER] 定时任务调度器已启动，jobs:", [j.id for j in scheduler.get_jobs()], flush=True)
