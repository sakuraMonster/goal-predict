"""系统管理 API"""
import asyncio
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.database import get_db
from app.db.models import TaskLog
from app.db.logger import AppLogger
from app.db.redis_client import cache_bump_version
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
    """手动触发赔率更新（SportMonks 通用赔率表）"""
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


@router.post("/sync-market-flow-odds")
async def trigger_sync_market_flow_odds(
    date: str = Query(None, description="YYYY-MM-DD 竞彩比赛日，不传默认今天起未来7天"),
    db: AsyncSession = Depends(get_db),
):
    """MarketFlow V2 赔率拉取：从竞彩网 getMatchCalculatorV1 拉实时赔率（HAD/HHAD/TTG/CRS 四玩法），写入 JczqPlayOddsSnapshot 表。

    MarketFlow V2 的预测逻辑只认 JczqPlayOddsSnapshot（竞彩收盘/实时赔率，带 TTG/CRS 进球与比分赔率），
    与通用 sync_odds 写入的 SportMonks odds_snapshots 表是两张完全不同的表。"""
    start = time.time()
    try:
        from tools import fetch_market_flow_odds_live as _live_mod
        from app.db.models import JczqPlayOddsSnapshot, Match
        import re as _re
        from datetime import datetime as _dt

        today = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if date:
            try:
                start_dt = _dt.strptime(date, "%Y-%m-%d")
            except ValueError:
                return {"status": "error", "message": "date 格式错误，应为 YYYY-MM-DD"}
        else:
            start_dt = today
        start_dt = start_dt.replace(hour=12, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=7)).replace(hour=11, minute=59, second=59, microsecond=0)

        live = _live_mod.fetch_live()
        parsed = []
        for raw in live:
            p = _live_mod._parse_match(raw)
            if not p:
                continue
            if not (p["kickoff"] and start_dt <= p["kickoff"] <= end_dt):
                continue
            parsed.append(p)

        inserted = 0
        updated = 0
        skipped_dup = 0
        skipped_unmatched = 0
        matched_local_ids: list[int] = []
        for p in parsed:
            matched = None
            r1 = await db.execute(select(Match).where(Match.jc_match_id == p["jc_match_id"]))
            matched = r1.scalar_one_or_none()
            if not matched and p["match_num"]:
                r2 = await db.execute(
                    select(Match).where(
                        Match.match_num == p["match_num"],
                        Match.kickoff_time >= (p["kickoff"] - timedelta(hours=12)) if p["kickoff"] else True,
                        Match.kickoff_time <= (p["kickoff"] + timedelta(hours=12)) if p["kickoff"] else True,
                    )
                )
                matched = r2.scalars().first()
            if not matched:
                skipped_unmatched += 1
                continue
            if matched.id in matched_local_ids:
                continue
            matched_local_ids.append(matched.id)

            existing = (await db.execute(
                select(JczqPlayOddsSnapshot).where(
                    JczqPlayOddsSnapshot.match_id == matched.id,
                    JczqPlayOddsSnapshot.source == "sporttery",
                )
            )).scalars().all()
            if any(_live_mod._snap_equal(s, p["had"], p["hhad"], p["ttg"], p["crs"]) for s in existing):
                matched_snaps = [s for s in existing if _live_mod._snap_equal(s, p["had"], p["hhad"], p["ttg"], p["crs"])]
                target = matched_snaps[0]
                # 历史快照 hafu 为空而本次拉到 hafu → 仅回填 hafu 字段（半全场赔率）
                if p["hafu"] and not target.hafu_odds_json:
                    target.hafu_odds_json = p["hafu"]
                    updated += 1
                    continue
                skipped_dup += 1
                continue
            db.add(JczqPlayOddsSnapshot(
                match_id=matched.id,
                snapshot_time=p["snapshot_time"],
                source="sporttery",
                had_home=p["had"]["home"], had_draw=p["had"]["draw"], had_away=p["had"]["away"],
                hhad_line=p["hhad"]["line"], hhad_home=p["hhad"]["home"],
                hhad_draw=p["hhad"]["draw"], hhad_away=p["hhad"]["away"],
                ttg_odds_json=p["ttg"], crs_odds_json=p["crs"],
                hafu_odds_json=p["hafu"],
            ))
            inserted += 1
        await db.commit()
        await cache_bump_version()  # 竞彩赔率（含 hafu）写入 → 统计缓存失效

        duration = int((time.time() - start) * 1000)
        msg = (f"MarketFlow 赔率：API {len(live)} 场 → 窗口过滤 {len(parsed)} 场 → 写入 JczqPlayOddsSnapshot {inserted} 条"
               f"（回填半全场 {updated}）→ 重复跳过 {skipped_dup}，本地赛事无匹配 {skipped_unmatched}")
        await AppLogger.log("sync_odds", "success", msg, duration)
        return {"status": "ok", "message": msg, "data": {
            "api_total": len(live), "window_total": len(parsed),
            "inserted": inserted, "updated_hafu": updated,
            "skipped_dup": skipped_dup,
            "skipped_unmatched": skipped_unmatched,
        }}
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("sync_odds", "failed", f"MarketFlow赔率失败: {e}", duration)
        return {"status": "error", "message": str(e)}


# SportMonks O/U（大小球）market_id 常量 + 博彩公司名映射（与 tools/_backfill_sm_ou.py 一致）
_SM_OU_MID = 80
_SM_BOOKMAKER_NAMES = {
    1: "Pinnacle", 2: "SportPesa", 3: "SBK", 5: "Bet365",
    9: "Marathonbet", 12: "Betfair", 16: "1xBet", 20: "Betclic",
    23: "10Bet", 34: "BetVictor", 35: "Betting Exchange",
}


def _sm_safe_float(v):
    """宽松数值解析：处理 None/字符串含空格/逗号"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(" ", "").split(",")[0])
    except (ValueError, TypeError):
        return None


def _parse_sm_ou_odds(odds: list) -> tuple[dict, dict]:
    """解析 SportMonks 赛前赔率中的 O/U（大小球）行情。

    返回 (ou_rows, bm_names)：
      ou_rows   {(bookmaker_id, goal_line): {"over": 赔率, "under": 赔率}}，仅保留 0.5~6.5 有效线
      bm_names  {bookmaker_id: 博彩公司名}
    """
    ou_rows: dict = {}
    bm_names: dict = {}
    for o in odds:
        if o.get("market_id") != _SM_OU_MID:
            continue
        lab = str(o.get("label", "")).strip().lower()
        if lab not in ("over", "under"):
            continue
        line = _sm_safe_float(o.get("total"))
        if line is None or not (0.5 <= line <= 6.5):
            continue
        val = _sm_safe_float(o.get("value"))
        if val is None or val <= 1.0:
            continue
        bm_id = o.get("bookmaker_id")
        bm_name = _SM_BOOKMAKER_NAMES.get(bm_id)
        if not bm_name and isinstance(o.get("bookmaker"), dict):
            bm_name = o.get("bookmaker", {}).get("name")
        bm_name = bm_name or str(bm_id)
        bm_names[bm_id] = bm_name
        ou_rows.setdefault((bm_id, round(line, 2)), {})[lab] = val
    return ou_rows, bm_names


@router.post("/sync-market-flow-sm-odds")
async def trigger_sync_market_flow_sm_odds(
    date: str = Query(None, description="YYYY-MM-DD 比赛日，不传默认今天起未来7天"),
    db: AsyncSession = Depends(get_db),
):
    """MarketFlow V2 SM O/U 盘口拉取：从 SportMonks 拉赛前 O/U（大小球 over/under）赔率，写入 odds_snapshots 表。

    MarketFlow V2 的 O/U 盘口判断（ou_sm）读 odds_snapshots 中 goal_line=2.5 的 over_odds/under_odds 行
    （Pinnacle 优先、多博彩公司中位数），同时按整半线（0.5~6.5）独立判定并展示多线徽标。
    竞彩网 sync-market-flow-odds 只覆盖 HAD/HHAD/TTG/CRS，O/U 独立连续盘口需本端点从 SportMonks 补齐。
    已同步完整多线的比赛自动跳过（不重复拉取）；仅 2.5 单线或缺数据的比赛重新拉取补全其他盘口线。"""
    start = time.time()
    try:
        from app.collector.sportmonks.client import SportMonksClient
        from app.db.models import Match, OddsSnapshot
        from datetime import datetime as _dt

        today = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if date:
            try:
                start_dt = _dt.strptime(date, "%Y-%m-%d")
            except ValueError:
                return {"status": "error", "message": "date 格式错误，应为 YYYY-MM-DD"}
        else:
            start_dt = today
        start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=7)).replace(hour=23, minute=59, second=59, microsecond=0)

        # 窗口内本地比赛（含 7 天未来），需已匹配 SportMonks fixture
        r = await db.execute(
            select(Match).where(
                Match.kickoff_time >= start_dt,
                Match.kickoff_time <= end_dt,
                Match.sportmonks_fixture_id.is_not(None),
            )
        )
        matches = r.scalars().all()

        # 已有完整多线 O/U 数据的比赛 → 跳过；仅 2.5 单线或缺 O/U 数据的比赛重新拉取
        # （SM 越临近开赛盘口线越全，之前只拉到 2.5 的场次需重拉补全其他盘口线）
        mid_has_ou: set[int] = set()
        if matches:
            ro = await db.execute(
                select(OddsSnapshot.match_id, OddsSnapshot.goal_line)
                .distinct()
                .where(
                    OddsSnapshot.match_id.in_([m.id for m in matches]),
                    OddsSnapshot.over_odds.is_not(None),
                    OddsSnapshot.under_odds.is_not(None),
                    OddsSnapshot.goal_line >= 0.5,
                    OddsSnapshot.goal_line <= 6.5,
                )
            )
            per_mid: dict = {}
            for _mid, _gl in ro.all():
                per_mid.setdefault(_mid, set()).add(float(_gl))
            for _mid, _gls in per_mid.items():
                if 2.5 in _gls and len(_gls) >= 2:
                    mid_has_ou.add(_mid)

        sm = SportMonksClient()
        now = _dt.utcnow()
        total = len(matches)
        inserted = 0
        skipped_had = 0
        skipped_empty = 0
        skipped_no_match = 0
        errors = 0

        for m in matches:
            if m.id in mid_has_ou:
                skipped_had += 1
                continue
            try:
                odds = await sm.get_odds_pre_match(m.sportmonks_fixture_id)
            except Exception as e:
                errors += 1
                print(f"[SM-OU] err mid={m.id} fx={m.sportmonks_fixture_id} {type(e).__name__}: {e}", flush=True)
                continue
            ou_rows, bm_names = _parse_sm_ou_odds(odds)
            if not ou_rows:
                skipped_empty += 1
                continue
            if not ou_rows.get((1, 2.5)) and not any(abs(gl - 2.5) <= 1e-9 for (_, gl) in ou_rows):
                skipped_no_match += 1
            for (bm_id, gl), sides in ou_rows.items():
                db.add(OddsSnapshot(
                    match_id=m.id,
                    snapshot_time=now,
                    bookmaker=bm_names.get(bm_id, str(bm_id)),
                    over_odds=_sm_safe_float(sides.get("over")),
                    goal_line=gl,
                    under_odds=_sm_safe_float(sides.get("under")),
                    is_opening=False,
                ))
                inserted += 1
        await db.commit()
        await cache_bump_version()  # SportMonks O/U 写入 → 统计缓存失效
        await sm.close()

        duration = int((time.time() - start) * 1000)
        msg = (f"SM O/U：窗口 {total} 场（完整多线跳过 {skipped_had}，无O/U行情 {skipped_empty}，"
               f"无2.5线 {skipped_no_match}，拉取失败 {errors}）→ 写入 odds_snapshots {inserted} 行")
        await AppLogger.log("sync_odds", "success", msg, duration)
        return {"status": "ok", "message": msg, "data": {
            "window_total": total, "inserted": inserted,
            "skipped_had": skipped_had, "skipped_empty": skipped_empty,
            "skipped_no_match": skipped_no_match, "errors": errors,
        }}
    except Exception as e:
        duration = int((time.time() - start) * 1000)
        await AppLogger.log("sync_odds", "failed", f"SM O/U拉取失败: {e}", duration)
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
                pred.expected_goals_c = pred_result.get("expected_goals_c")
                pred.expected_goals_d = pred_result.get("expected_goals_d")
                pred.snap_top2_c = pred_result.get("snap_top2_c")
                pred.snap_top2_d = pred_result.get("snap_top2_d")
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
                    expected_goals_c=pred_result.get("expected_goals_c"),
                    expected_goals_d=pred_result.get("expected_goals_d"),
                    snap_top2_c=pred_result.get("snap_top2_c"),
                    snap_top2_d=pred_result.get("snap_top2_d"),
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


@router.post("/predict-market-flow")
async def predict_market_flow_batch(
    date: str = Query(None, description="日期 YYYY-MM-DD，不传默认今天（竞彩比赛日）"),
    overwrite: bool = Query(False, description="是否覆盖已存在的预测"),
    db: AsyncSession = Depends(get_db),
):
    """MarketFlow V2 批量预测：对指定比赛日（当日12:00 ~ 次日12:00）的所有比赛执行方向+比分预测"""
    from app.db.models import Match, Team, MarketFlowPrediction, JczqPlayOddsSnapshot, League
    from app.predictor.models.market_flow import MarketFlowEngineV2
    from app.api.market_flow import (
        _validate_engine_result,
        _apply_v3e_postprocess,
        _mk_mfp_denorm,
    )
    from sqlalchemy.orm import joinedload

    start_time = time.time()

    if date:
        try:
            d0 = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return {"status": "error", "message": "date 格式错误，应为 YYYY-MM-DD"}
        query_start = d0.replace(hour=12, minute=0, second=0, microsecond=0)
        query_end = query_start + timedelta(days=1)
    else:
        now = datetime.now(BEIJING_TZ).replace(tzinfo=None)
        query_start = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if query_start > now:
            query_start = query_start - timedelta(days=1)
        query_end = query_start + timedelta(days=1)

    result = await db.execute(
        select(Match)
        .options(
            joinedload(Match.home_team),
            joinedload(Match.away_team),
        )
        .where(
            Match.kickoff_time >= query_start,
            Match.kickoff_time < query_end,
        )
        .order_by(Match.kickoff_time.asc())
    )
    matches = list(result.unique().scalars().all())

    if not matches:
        return {"status": "ok", "message": f"{date or '今日'} 无比赛数据"}

    engine = MarketFlowEngineV2()
    model_version = "marketflow_v2_tuned7_0821"
    created = 0
    updated = 0
    skipped = 0
    failed = 0
    skipped_reasons: dict[str, int] = {}

    for m in matches:
        try:
            snap_stmt = (
                select(JczqPlayOddsSnapshot)
                .where(JczqPlayOddsSnapshot.match_id == m.id)
                .order_by(JczqPlayOddsSnapshot.snapshot_time.desc())
                .limit(1)
            )
            snap_result = await db.execute(snap_stmt)
            snap = snap_result.scalar_one_or_none()
            if not snap:
                skipped += 1
                skipped_reasons["missing_odds_snapshot"] = skipped_reasons.get("missing_odds_snapshot", 0) + 1
                continue

            existing_result = await db.execute(
                select(MarketFlowPrediction).where(MarketFlowPrediction.match_id == m.id)
            )
            pred = existing_result.scalar_one_or_none()

            if pred and not overwrite:
                skipped += 1
                skipped_reasons["prediction_exists"] = skipped_reasons.get("prediction_exists", 0) + 1
                continue

            home_style_tag = "均衡"
            away_style_tag = "均衡"
            if m.home_team and m.home_team.style_tag:
                home_style_tag = m.home_team.style_tag
            if m.away_team and m.away_team.style_tag:
                away_style_tag = m.away_team.style_tag

            had = {"home": snap.had_home, "draw": snap.had_draw, "away": snap.had_away}
            hhad = {
                "line": snap.hhad_line,
                "home": snap.hhad_home,
                "draw": snap.hhad_draw,
                "away": snap.hhad_away,
            }
            ttg = snap.ttg_odds_json or {}
            crs = snap.crs_odds_json or {}

            if not (had["home"] and had["draw"] and had["away"]):
                skipped += 1
                skipped_reasons["incomplete_had_odds"] = skipped_reasons.get("incomplete_had_odds", 0) + 1
                continue
            if not (hhad["home"] and hhad["draw"] and hhad["away"]):
                skipped += 1
                skipped_reasons["incomplete_hhad_odds"] = skipped_reasons.get("incomplete_hhad_odds", 0) + 1
                continue
            if not ttg or not crs:
                skipped += 1
                skipped_reasons["missing_ttg_or_crs"] = skipped_reasons.get("missing_ttg_or_crs", 0) + 1
                continue

            engine_result = engine.predict(
                home_style_tag=home_style_tag,
                away_style_tag=away_style_tag,
                had=had,
                hhad=hhad,
                ttg=ttg,
                crs=crs,
            )

            ok, missing = _validate_engine_result(engine_result)
            if not ok:
                failed += 1
                skipped_reasons[f"engine_missing_{','.join(missing)}"] = skipped_reasons.get(f"engine_missing_{','.join(missing)}", 0) + 1
                continue

            league_name_zh = None
            if m.league_id:
                _lg_res = await db.execute(select(League.name_zh).where(League.id == m.league_id))
                league_name_zh = _lg_res.scalar_one_or_none()
            engine_result = _apply_v3e_postprocess(engine_result, snap, league_name_zh=league_name_zh, matchday_date=getattr(m, "matchday_date", None))

            now_utc = datetime.utcnow()
            denorm = _mk_mfp_denorm(m)

            if pred:
                pred.odds_snapshot_id = snap.id
                pred.model_version = model_version
                pred.created_at = now_utc
                pred.home_style_tag = home_style_tag
                pred.away_style_tag = away_style_tag
                pred.best_total_goals = engine_result.get("best_total_goals")
                pred.second_total_goals = engine_result.get("second_total_goals")
                pred.best_score = engine_result.get("best_score")
                pred.second_score = engine_result.get("second_score")
                pred.trace_json = engine_result.get("trace")
                pred.league_id = denorm["league_id"]
                pred.kickoff_time = denorm["kickoff_time"]
                pred.matchday_date = denorm["matchday_date"]
                updated += 1
            else:
                db.add(MarketFlowPrediction(
                    match_id=m.id,
                    odds_snapshot_id=snap.id,
                    model_version=model_version,
                    created_at=now_utc,
                    home_style_tag=home_style_tag,
                    away_style_tag=away_style_tag,
                    best_total_goals=engine_result.get("best_total_goals"),
                    second_total_goals=engine_result.get("second_total_goals"),
                    best_score=engine_result.get("best_score"),
                    second_score=engine_result.get("second_score"),
                    trace_json=engine_result.get("trace"),
                    league_id=denorm["league_id"],
                    kickoff_time=denorm["kickoff_time"],
                    matchday_date=denorm["matchday_date"],
                ))
                created += 1

        except Exception as e:
            failed += 1
            skipped_reasons[f"exception: {type(e).__name__}"] = skipped_reasons.get(f"exception: {type(e).__name__}", 0) + 1
            if failed <= 5:
                print(f"[predict-market-flow] match_id={m.id} 失败: {e}", flush=True)
            continue

    try:
        await db.commit()
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        duration = int((time.time() - start_time) * 1000)
        await AppLogger.log("predict", "failed", f"MarketFlow 批量预测失败: {e}", duration)
        return {"status": "error", "message": f"保存数据库失败: {e}"}
    await cache_bump_version()  # MarketFlow 预测写入 → 统计缓存失效

    duration = int((time.time() - start_time) * 1000)
    msg = f"MarketFlow V2 预测完成: 新增 {created}, 更新 {updated}, 跳过 {skipped}, 失败 {failed}（共 {len(matches)} 场）"
    await AppLogger.log("predict", "success", msg, duration)
    return {
        "status": "ok",
        "message": msg,
        "data": {
            "total": len(matches),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "skipped_reasons": skipped_reasons,
        }
    }
