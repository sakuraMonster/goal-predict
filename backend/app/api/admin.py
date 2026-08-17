"""系统管理 API"""
import asyncio
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.database import get_db
from app.db.models import TaskLog
from app.db.logger import AppLogger
from app.collector.pipeline import SyncPipeline
from datetime import datetime, timedelta, timezone
import time

BEIJING_TZ = timezone(timedelta(hours=8))


def _to_beijing_str(dt: datetime | None) -> str | None:
    """将 UTC naive datetime 转为北京时间字符串"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


router = APIRouter(prefix="/api/admin", tags=["admin"])


def _get_latest_task(db_session, task_types: list):
    """helper for getting latest task status"""


@router.get("/task-status")
async def get_task_status(db: AsyncSession = Depends(get_db)):
    """定时任务状态（从 task_logs 查最近执行记录）"""
    task_types = ["sync_matches", "sync_odds", "update_teams", "refresh_opening", "retrain", "predict"]
    freq_map = {
        "sync_matches": "每日 09:00 / 12:00",
        "sync_odds": "每 30 分钟",
        "update_teams": "每日 03:00",
        "refresh_opening": "手动触发",
        "retrain": "每周一 04:00",
        "predict": "手动触发",
    }

    data = []
    for tt in task_types:
        result = await db.execute(
            select(TaskLog).where(TaskLog.task_type == tt).order_by(TaskLog.start_time.desc()).limit(1)
        )
        latest = result.scalar_one_or_none()
        data.append({
            "task_type": tt,
            "status": latest.status if latest else "idle",
            "last_run": _to_beijing_str(latest.start_time) if latest else None,
            "duration_ms": latest.duration_ms if latest else 0,
            "freq": freq_map.get(tt, ""),
            "last_message": latest.message if latest else "",
        })
    return {"data": data}


@router.get("/logs")
async def get_logs(hours: int = Query(48), db: AsyncSession = Depends(get_db)):
    """操作日志（最近 N 小时内）"""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    result = await db.execute(
        select(TaskLog)
        .where(TaskLog.created_at >= cutoff, TaskLog.task_type != "api_request")
        .order_by(TaskLog.created_at.desc())
        .limit(200)
    )
    logs = result.scalars().all()
    return {"data": [{"task_type": l.task_type, "status": l.status, "message": l.message,
                       "created_at": _to_beijing_str(l.created_at), "duration_ms": l.duration_ms} for l in logs]}


@router.get("/scheduler-status")
async def get_scheduler_status():
    """诊断：查看 APScheduler 是否在运行及任务状态"""
    from app.scheduler import scheduler
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": _to_beijing_str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return {
        "scheduler_running": scheduler.running,
        "jobs": jobs,
    }


async def _run_sync_matches():
    pipeline = SyncPipeline()
    await pipeline.sync_daily_matches()


async def _run_sync_odds():
    pipeline = SyncPipeline()
    await pipeline.sync_odds()


async def _run_update_teams():
    pipeline = SyncPipeline()
    await pipeline.sync_team_info()


@router.post("/sync-matches")
async def trigger_sync_matches():
    """手动触发赛程同步"""
    start = time.time()
    try:
        await _run_sync_matches()
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("sync_matches", "success", "手动同步赛程完成", duration)
        return {"status": "ok", "message": "赛程同步已完成"}
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("sync_matches", "failed", f"同步失败: {e}", duration)
        return {"status": "error", "message": str(e)}


@router.post("/sync-odds")
async def trigger_sync_odds():
    """手动触发赔率更新"""
    start = time.time()
    try:
        await _run_sync_odds()
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("sync_odds", "success", "手动赔率更新完成", duration)
        return {"status": "ok", "message": "赔率更新已完成"}
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("sync_odds", "failed", f"更新失败: {e}", duration)
        return {"status": "error", "message": str(e)}


@router.post("/update-teams")
async def trigger_update_teams():
    """手动触发球队信息更新（后台异步执行）"""
    async def _run():
        print("[ADMIN] 后台球队更新任务开始...", flush=True)
        start = time.time()
        pipeline = SyncPipeline()
        try:
            await pipeline.sync_team_info()
            duration = int((time.time() - start) * 1000)
            await AppLogger.log("update_teams", "success", "球队信息更新完成", duration)
            print(f"[ADMIN] 后台球队更新任务完成，耗时 {duration}ms", flush=True)
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            await AppLogger.log("update_teams", "failed", f"更新失败: {e}", duration)
            print(f"[ADMIN] 后台球队更新任务失败: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            await pipeline.sm.close()

    asyncio.create_task(_run())
    print("[ADMIN] 后台球队更新任务已提交", flush=True)
    return {"status": "ok", "message": "球队信息更新任务已提交，将在后台执行"}


@router.post("/match-fixtures")
async def trigger_match_fixtures():
    """手动触发 SportMonks 赛事匹配"""
    start = time.time()
    try:
        pipeline = SyncPipeline()
        await pipeline.match_to_sportmonks()
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("match_fixtures", "success", "手动赛事匹配完成", duration)
        return {"status": "ok", "message": "SportMonks 赛事匹配已完成"}
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("match_fixtures", "failed", f"匹配失败: {e}", duration)
        return {"status": "error", "message": str(e)}


@router.post("/refresh-opening")
async def trigger_refresh_opening():
    """手动刷新初盘赔率：将当前所有赛事的最新快照标记为初盘"""
    from sqlalchemy import text
    from app.db.database import async_session
    start = time.time()
    try:
        async with async_session() as db:
            # 先清除所有初盘标记
            await db.execute(text("UPDATE odds_snapshots SET is_opening = FALSE"))
            
            # 重新标记：每个 (match_id, bookmaker) 最早一条为初盘
            await db.execute(text("""
                UPDATE odds_snapshots SET is_opening = TRUE
                WHERE id IN (
                    SELECT DISTINCT ON (match_id, bookmaker) id
                    FROM odds_snapshots
                    ORDER BY match_id, bookmaker, snapshot_time ASC
                )
            """))
            await db.commit()
            
            # 统计
            r = await db.execute(text("SELECT COUNT(*) FROM odds_snapshots WHERE is_opening = TRUE"))
            count = r.scalar()
        
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("refresh_opening", "success", f"已刷新 {count} 条初盘赔率", duration)
        return {"status": "ok", "message": f"已刷新 {count} 条初盘赔率"}
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("refresh_opening", "failed", str(e), duration)
        return {"status": "error", "message": str(e)}


@router.post("/trigger-retrain")
async def trigger_retrain():
    """触发模型重训练"""
    await AppLogger.log("retrain", "running", "收到重训练请求，当前数据量不足")
    return {"status": "ok", "message": "模型重训练需积累足够数据后执行，当前数据量不足"}


@router.post("/trigger-predict")
async def trigger_predict():
    """触发当天比赛预测（仅预测未来48h内赛事）"""
    from datetime import datetime, timedelta
    from app.db.database import async_session
    from app.db.models import Match, Prediction
    from app.predictor.pipeline import PredictionPipeline
    from sqlalchemy import select

    start_time = time.time()
    async with async_session() as db:
        # kickoff_time 存北京 naive，直接与北京当前时间比较（修正 UTC naive 偏移 8h 导致覆盖已开赛比赛的问题）
        now_bj = datetime.now(BEIJING_TZ).replace(tzinfo=None)
        cutoff = now_bj + timedelta(hours=48)
        result = await db.execute(
            select(Match).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.status == "scheduled",
                Match.kickoff_time >= now_bj,
                Match.kickoff_time <= cutoff,
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())

        if not matches:
            return {"status": "ok", "message": "未来48h内无待预测赛事"}

        pipeline = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")
        created, updated, failed = 0, 0, 0

        for m in matches:
            try:
                pred_result = await pipeline.predict(m.id)
            except Exception as e:
                failed += 1
                if failed <= 3:
                    print(f"[predict] match_id={m.id} 预测失败: {e}", flush=True)
                continue

            existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = existing.scalar_one_or_none()

            if pred:
                pred.home_prob = pred_result["home_prob"]
                pred.draw_prob = pred_result["draw_prob"]
                pred.away_prob = pred_result["away_prob"]
                pred.handicap_home_prob = pred_result["handicap_home_prob"]
                pred.handicap_draw_prob = pred_result["handicap_draw_prob"]
                pred.handicap_away_prob = pred_result["handicap_away_prob"]
                pred.expected_goals = pred_result["expected_goals"]
                pred.over_2_5_prob = pred_result["over_2_5_prob"]
                pred.goal_distribution = pred_result["goal_distribution"]
                pred.snap_top2 = pred_result["snap_top2"]
                pred.score_top5_json = pred_result["score_top5_json"]
                pred.confidence_level = pred_result["confidence_level"]
                pred.is_cold_match = pred_result["is_cold_match"]
                pred.cold_correction = pred_result.get("cold_correction")
                pred.summary_text = pred_result["summary_text"]
                pred.key_factors = pred_result.get("key_factors", "")
                pred.model_version = version
                pred.kickoff_time = m.kickoff_time
                pred.league_id = m.league_id
                updated += 1
            else:
                db.add(Prediction(
                    match_id=m.id, model_version=version,
                    home_prob=pred_result["home_prob"],
                    draw_prob=pred_result["draw_prob"],
                    away_prob=pred_result["away_prob"],
                    handicap_home_prob=pred_result["handicap_home_prob"],
                    handicap_draw_prob=pred_result["handicap_draw_prob"],
                    handicap_away_prob=pred_result["handicap_away_prob"],
                    expected_goals=pred_result["expected_goals"],
                    over_2_5_prob=pred_result["over_2_5_prob"],
                    goal_distribution=pred_result["goal_distribution"],
                    snap_top2=pred_result["snap_top2"],
                    score_top5_json=pred_result["score_top5_json"],
                    confidence_level=pred_result["confidence_level"],
                    is_cold_match=pred_result["is_cold_match"],
                    cold_correction=pred_result.get("cold_correction"),
                    summary_text=pred_result["summary_text"],
                    key_factors=pred_result.get("key_factors", ""),
                    kickoff_time=m.kickoff_time,
                    league_id=m.league_id,
                ))
                created += 1

        await db.commit()
        duration = int((time.time() - start_time) * 1000)
        msg = f"预测完成: 新增 {created}, 更新 {updated}, 失败 {failed}（共 {len(matches)} 场）"
        await AppLogger.log("predict", "success", msg, duration)
        return {"status": "ok", "message": msg}


@router.post("/predict-model-c")
async def predict_model_c():
    """对当天比赛场次仅使用 Model C 进行预测，不触发 Model A/B/D"""
    from app.predictor.models.model_c import ModelC
    from app.predictor.features_b import FeatureEngineerB
    from app.predictor.pipeline import PredictionPipeline
    from app.predictor.snap import snap_top2
    from app.db.database import async_session
    from app.db.models import Match, Prediction, League
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    now = datetime.now(BEIJING_TZ)
    # 查询窗口：当前比赛日 ~ 未来48h内的所有待预测比赛
    # 比赛日边界 = 当天12:00，当前时间在12:00前则回退到昨天12:00
    query_start = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now.hour < 12:
        query_start = query_start - timedelta(days=1)
    query_end = query_start + timedelta(hours=72)  # 覆盖3个比赛日，确保不漏

    start_time = time.time()
    async with async_session() as db:
        result = await db.execute(
            select(Match).options(joinedload(Match.league)).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.status == "scheduled",
                # 只预测尚未开赛的场次（kickoff_time 存北京 naive，与 now 北京 naive 直接比较）
                Match.kickoff_time > now.replace(tzinfo=None),
                Match.kickoff_time >= query_start.astimezone(timezone.utc).replace(tzinfo=None),
                Match.kickoff_time < query_end.astimezone(timezone.utc).replace(tzinfo=None),
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.unique().scalars().all())

        if not matches:
            return {"status": "ok", "message": "当前比赛日无待预测赛事"}

        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()
        version = datetime.now().strftime("%Y%m%d-%H%M")
        updated, created, failed = 0, 0, 0

        for m in matches:
            league_name = m.league.name_zh if m.league else None

            try:
                features_df = await feat_engine.extract_features(m.id)
                if features_df.empty:
                    failed += 1
                    continue
                features = features_df.iloc[0].to_dict()
                result_c = model_c.predict(features, league_name)
                # V4.13: Model C 专属后验校准（与 pipeline.predict 保持一致，防止两个入口口径不一致）
                mc_calib = PredictionPipeline.MODELC_LAMBDA_CALIBRATION.get(league_name)
                if mc_calib:
                    result_c = PredictionPipeline._apply_lambda_calibration(result_c, mc_calib)
            except Exception as e:
                failed += 1
                if failed <= 3:
                    print(f"[predict-model-c] match_id={m.id} 预测失败: {e}", flush=True)
                continue

            expected_goals_c = result_c["expected_goals"]
            snap_c = snap_top2(expected_goals_c)

            existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = existing.scalar_one_or_none()

            if pred:
                pred.expected_goals_c = expected_goals_c
                pred.snap_top2_c = snap_c
                updated += 1
            else:
                db.add(Prediction(
                    match_id=m.id,
                    model_version=version,
                    expected_goals_c=expected_goals_c,
                    snap_top2_c=snap_c,
                    kickoff_time=m.kickoff_time,
                    league_id=m.league_id,
                ))
                created += 1

        await db.commit()
        duration = int((time.time() - start_time) * 1000)
        msg = f"Model C 预测完成: 新增 {created}, 更新 {updated}, 失败 {failed}（共 {len(matches)} 场）"
        await AppLogger.log("predict", "success", msg, duration)
        return {"status": "ok", "message": msg}


@router.post("/repredict-model-c")
async def repredict_model_c(
    date: str = Query(..., description="日期 YYYY-MM-DD"),
):
    """Model C 重预测：对指定比赛日所有比赛重新执行 Model C 进球数预测（expected_goals_c/snap_top2_c）。

    与 predict-model-c 的区别：后者只处理未开赛场次；本端点按用户主动选择的历史比赛日全量重算，
    用于修正/补算历史预测值。
    """

    from app.predictor.models.model_c import ModelC
    from app.predictor.features_b import FeatureEngineerB
    from app.predictor.pipeline import PredictionPipeline
    from app.predictor.snap import snap_top2
    from app.db.database import async_session
    from app.db.models import Match, Prediction
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    d_start = datetime.strptime(date, "%Y-%m-%d")
    query_start = d_start.replace(hour=12)
    query_end = (d_start + timedelta(days=1)).replace(hour=12)

    start_time = time.time()
    async with async_session() as db:
        result = await db.execute(
            select(Match).options(joinedload(Match.league)).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.kickoff_time >= query_start,
                Match.kickoff_time < query_end,
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.unique().scalars().all())

        if not matches:
            return {"status": "ok", "message": f"{date} 无比赛数据"}

        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()
        version = datetime.now().strftime("%Y%m%d-%H%M")
        updated, created, failed = 0, 0, 0

        for m in matches:
            league_name = m.league.name_zh if m.league else None

            try:
                features_df = await feat_engine.extract_features(m.id)
                if features_df.empty:
                    failed += 1
                    continue
                features = features_df.iloc[0].to_dict()
                result_c = model_c.predict(features, league_name)
                # Model C 专属后验校准（与 predict-model-c / pipeline.predict 口径一致）
                mc_calib = PredictionPipeline.MODELC_LAMBDA_CALIBRATION.get(league_name)
                if mc_calib:
                    result_c = PredictionPipeline._apply_lambda_calibration(result_c, mc_calib)
            except Exception as e:
                failed += 1
                if failed <= 3:
                    print(f"[repredict-model-c] match_id={m.id} 预测失败: {e}", flush=True)
                continue

            expected_goals_c = result_c["expected_goals"]
            snap_c = snap_top2(expected_goals_c)

            existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = existing.scalar_one_or_none()

            if pred:
                pred.expected_goals_c = expected_goals_c
                pred.snap_top2_c = snap_c
                updated += 1
            else:
                db.add(Prediction(
                    match_id=m.id,
                    model_version=version,
                    expected_goals_c=expected_goals_c,
                    snap_top2_c=snap_c,
                    kickoff_time=m.kickoff_time,
                    league_id=m.league_id,
                ))
                created += 1

        await db.commit()
        duration = int((time.time() - start_time) * 1000)
        msg = f"Model C 重预测完成: 新增 {created}, 更新 {updated}, 失败 {failed}（共 {len(matches)} 场）"
        await AppLogger.log("predict", "success", msg, duration)
        return {
            "status": "ok", "message": msg,
            "version": version,
            "updated": updated, "created": created, "failed": failed,
        }


@router.post("/repredict-model-b")
async def repredict_model_b(
    date: str = Query(..., description="日期 YYYY-MM-DD"),
    full: bool = Query(False, description="是否完整重跑 pipeline（会覆盖历史 expected_goals，默认仅回填 snap_top2）"),
):
    """Model B 重预测：默认仅回填 snap_top2 + result_goals，不动已有预测值"""

    from app.predictor.snap import snap_top2
    from app.db.models import Prediction
    from sqlalchemy import select
    from app.db.database import async_session

    d_start = datetime.strptime(date, "%Y-%m-%d")
    query_start = d_start.replace(hour=12)
    query_end = (d_start + timedelta(days=1)).replace(hour=12)

    start_time = time.time()
    async with async_session() as db:
        if full:
            # 完整重跑 pipeline
            from app.predictor.pipeline import PredictionPipeline
            result = await db.execute(
                select(Match).where(
                    Match.status == "scheduled",
                    Match.kickoff_time >= query_start,
                    Match.kickoff_time < query_end,
                ).order_by(Match.kickoff_time)
            )
            matches = list(result.scalars().all())
            if not matches:
                return {"status": "ok", "message": f"{date} 无比赛数据"}

            pipeline = PredictionPipeline(db)
            model_version = datetime.now().strftime("%Y%m%d-%H%M")
            updated, failed, skipped = 0, 0, 0

            for m in matches:
                try:
                    pred_result = await pipeline.predict(m.id)
                except Exception:
                    failed += 1
                    continue
                existing = await db.execute(
                    select(Prediction).where(Prediction.match_id == m.id)
                )
                pred = existing.scalar_one_or_none()
                if not pred:
                    skipped += 1
                    continue
                pred.expected_goals = pred_result["expected_goals"]
                pred.over_2_5_prob = pred_result["over_2_5_prob"]
                pred.goal_distribution = pred_result["goal_distribution"]
                pred.snap_top2 = pred_result["snap_top2"]
                pred.score_top5_json = pred_result["score_top5_json"]
                pred.summary_text = pred_result.get("summary_text", "")
                pred.key_factors = pred_result.get("key_factors", "")
                pred.model_version = model_version
                if pred.actual_total_goals is not None and pred.snap_top2:
                    pred.result_goals = 1 if pred.actual_total_goals in pred.snap_top2 else -1
                updated += 1
        else:
            # 安全模式：仅回填 snap_top2
            result = await db.execute(
                select(Prediction).where(
                    Prediction.kickoff_time >= query_start,
                    Prediction.kickoff_time < query_end,
                )
            )
            preds = list(result.scalars().all())
            if not preds:
                return {"status": "ok", "message": f"{date} 无预测数据"}

            model_version = datetime.now().strftime("%Y%m%d-%H%M")
            updated, failed, skipped = 0, 0, 0

            for p in preds:
                if not p.expected_goals:
                    skipped += 1
                    continue
                p.snap_top2 = snap_top2(p.expected_goals)
                if p.actual_total_goals is not None:
                    p.result_goals = 1 if p.actual_total_goals in p.snap_top2 else -1
                updated += 1

        await db.commit()
        duration = int((time.time() - start_time) * 1000)
        mode = "完整重跑" if full else "回填 snap_top2"
        msg = f"Model B {mode}: 更新 {updated}, 跳过 {skipped}, 失败 {failed}"
        await AppLogger.log("predict", "success", msg, duration)
        return {
            "status": "ok", "message": msg,
            "version": model_version,
            "updated": updated, "skipped": skipped, "failed": failed,
            "mode": "full" if full else "backfill",
        }
