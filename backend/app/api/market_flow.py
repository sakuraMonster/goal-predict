from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import JczqPlayOddsSnapshot, League, MarketFlowPrediction, Match, OddsSnapshot, Prediction, Team
from app.predictor.models.market_flow import MarketFlowEngine, MarketFlowEngineV2, _hhad_signal_v2
from app.predictor.models.ou_direction import DEFAULT_TIER, TIERS, ou_direction_all_tiers
from app.predictor.models.ou_market import (
    DEFAULT_TIER as OU_M_DEFAULT,
    TIERS as OU_M_TIERS,
    ou_market_from_rows,
)
from app.predictor.snap import snap_top2

router = APIRouter(prefix="/api/market-flow", tags=["market_flow"])


def _err(msg: str, code: int):
    return JSONResponse(status_code=code, content={"error": msg})


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(tzinfo=None)


def _as_float(v: object) -> float | None:
    if isinstance(v, (int, float)) and float(v) > 0:
        return float(v)
    return None


def _norm_score(v: object) -> str | None:
    if not isinstance(v, str) or not v:
        return None
    out = v.strip().replace(":", "-")
    return out or None


def _validate_engine_result(result: object) -> tuple[bool, list[str]]:
    if not isinstance(result, dict):
        return False, ["result"]
    missing: list[str] = []
    for key in ["best_total_goals", "second_total_goals"]:
        if key not in result or result.get(key) is None:
            missing.append(key)
    for key in ["best_score", "second_score"]:
        v = result.get(key) if key in result else None
        if not isinstance(v, str) or not v.strip():
            missing.append(key)
    return (len(missing) == 0), missing


def _mk_mfp_denorm(match: Match) -> dict:
    """从 Match 抽出 MarketFlowPrediction 的三列冗余：league_id / kickoff_time / matchday_date。
    matchday_date = 竞彩口径比赛日：kickoff_time -12h 取 date（早上 10:00 之前 = 算上一比赛日）
    """
    kdt = match.kickoff_time if isinstance(match.kickoff_time, datetime) else None
    if kdt is None:
        return {"league_id": match.league_id, "kickoff_time": None, "matchday_date": None}
    md_date = (kdt - timedelta(hours=12)).date()
    return {"league_id": match.league_id, "kickoff_time": kdt, "matchday_date": md_date}


@router.post("/odds-snapshots")
async def create_odds_snapshot(payload: dict = Body(...), db: AsyncSession = Depends(get_db)):
    match_id = payload.get("match_id")
    if not isinstance(match_id, int):
        return _err("match_id must be int", 400)

    snapshot_time = _parse_dt(payload.get("snapshot_time"))
    if not snapshot_time:
        return _err("snapshot_time must be ISO string", 400)

    source = payload.get("source")
    if not isinstance(source, str) or not source:
        return _err("source must be string", 400)

    had = payload.get("had")
    if not isinstance(had, dict):
        return _err("had must be object", 400)
    had_home = _as_float(had.get("home"))
    had_draw = _as_float(had.get("draw"))
    had_away = _as_float(had.get("away"))
    if had_home is None or had_draw is None or had_away is None:
        return _err("had.home/draw/away must be positive numbers", 400)

    hhad = payload.get("hhad")
    if not isinstance(hhad, dict):
        return _err("hhad must be object", 400)
    line_raw = hhad.get("line")
    if not isinstance(line_raw, (int, float)):
        return _err("hhad.line must be number", 400)
    hhad_line = float(line_raw)
    hhad_home = _as_float(hhad.get("home"))
    hhad_draw = _as_float(hhad.get("draw"))
    hhad_away = _as_float(hhad.get("away"))
    if hhad_home is None or hhad_draw is None or hhad_away is None:
        return _err("hhad.home/draw/away must be positive numbers", 400)

    ttg = payload.get("ttg")
    if not isinstance(ttg, dict):
        return _err("ttg must be object", 400)
    if not ttg:
        return _err("ttg must be non-empty object", 400)

    crs = payload.get("crs")
    if not isinstance(crs, dict):
        return _err("crs must be object", 400)
    if not crs:
        return _err("crs must be non-empty object", 400)

    match_result = await db.execute(select(Match).where(Match.id == match_id))
    match = match_result.scalar_one_or_none()
    if not match:
        return _err("match not found", 404)

    snap = JczqPlayOddsSnapshot(
        match_id=match_id,
        snapshot_time=snapshot_time,
        source=source,
        had_home=had_home,
        had_draw=had_draw,
        had_away=had_away,
        hhad_line=hhad_line,
        hhad_home=hhad_home,
        hhad_draw=hhad_draw,
        hhad_away=hhad_away,
        ttg_odds_json=ttg,
        crs_odds_json=crs,
    )
    try:
        db.add(snap)
        await db.commit()
        await db.refresh(snap)
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        return _err("failed to write odds snapshot", 500)

    return {"data": {"odds_snapshot_id": snap.id}}


@router.post("/predict/{match_id}")
async def predict_market_flow(match_id: int, payload: dict = Body(...), db: AsyncSession = Depends(get_db)):
    odds_snapshot_id = payload.get("odds_snapshot_id")
    if not isinstance(odds_snapshot_id, int):
        return _err("odds_snapshot_id must be int", 400)

    engine_version = payload.get("engine_version") or "v2"
    if not isinstance(engine_version, str) or engine_version not in {"v1", "v2"}:
        return _err("engine_version must be v1 or v2", 400)

    config = payload.get("config")
    if engine_version == "v2" and config is not None and not isinstance(config, dict):
        return _err("config must be object", 400)

    model_version = payload.get("model_version")
    if model_version is None:
        model_version = "marketflow_v1" if engine_version == "v1" else "marketflow_v2"
    if not isinstance(model_version, str) or not model_version:
        return _err("model_version must be string", 400)

    match_result = await db.execute(select(Match).where(Match.id == match_id))
    match = match_result.scalar_one_or_none()
    if not match:
        return _err("match not found", 404)

    snap_result = await db.execute(select(JczqPlayOddsSnapshot).where(JczqPlayOddsSnapshot.id == odds_snapshot_id))
    snap = snap_result.scalar_one_or_none()
    if not snap:
        return _err("odds snapshot not found", 404)
    if snap.match_id != match_id:
        return _err("odds snapshot mismatch", 400)

    home_style_tag = "均衡"
    away_style_tag = "均衡"

    if match.home_team_id:
        home_team_result = await db.execute(select(Team).where(Team.id == match.home_team_id))
        home_team = home_team_result.scalar_one_or_none()
        if home_team and home_team.style_tag:
            home_style_tag = home_team.style_tag

    if match.away_team_id:
        away_team_result = await db.execute(select(Team).where(Team.id == match.away_team_id))
        away_team = away_team_result.scalar_one_or_none()
        if away_team and away_team.style_tag:
            away_style_tag = away_team.style_tag

    if engine_version == "v1":
        engine = MarketFlowEngine()
        result = engine.predict(
            home_style_tag=home_style_tag,
            away_style_tag=away_style_tag,
            had={"home": snap.had_home, "draw": snap.had_draw, "away": snap.had_away},
            hhad={
                "line": snap.hhad_line,
                "home": snap.hhad_home,
                "draw": snap.hhad_draw,
                "away": snap.hhad_away,
            },
            ttg=snap.ttg_odds_json or {},
            crs=snap.crs_odds_json or {},
        )
    else:
        engine = MarketFlowEngineV2()
        result = engine.predict(
            home_style_tag=home_style_tag,
            away_style_tag=away_style_tag,
            had={"home": snap.had_home, "draw": snap.had_draw, "away": snap.had_away},
            hhad={
                "line": snap.hhad_line,
                "home": snap.hhad_home,
                "draw": snap.hhad_draw,
                "away": snap.hhad_away,
            },
            ttg=snap.ttg_odds_json or {},
            crs=snap.crs_odds_json or {},
            config=config,
        )
    ok, missing = _validate_engine_result(result)
    if not ok:
        return _err(f"marketflow engine result missing fields: {','.join(missing)}", 400)

    # 查弱联赛名称（TOP1弱联赛惩罚用）
    league_name_zh = None
    if match.league_id:
        _lg_res = await db.execute(select(League.name_zh).where(League.id == match.league_id))
        league_name_zh = _lg_res.scalar_one_or_none()

    result = _apply_v3e_postprocess(result, snap, league_name_zh=league_name_zh, matchday_date=getattr(match, "matchday_date", None))

    pred_result = await db.execute(select(MarketFlowPrediction).where(MarketFlowPrediction.match_id == match_id))
    pred = pred_result.scalar_one_or_none()

    denorm = _mk_mfp_denorm(match)
    now = datetime.utcnow()
    if pred:
        pred.odds_snapshot_id = odds_snapshot_id
        pred.model_version = model_version
        pred.created_at = now
        pred.home_style_tag = home_style_tag
        pred.away_style_tag = away_style_tag
        pred.best_total_goals = result.get("best_total_goals")
        pred.second_total_goals = result.get("second_total_goals")
        pred.best_score = result.get("best_score")
        pred.second_score = result.get("second_score")
        pred.trace_json = result.get("trace")
        pred.league_id = denorm["league_id"]
        pred.kickoff_time = denorm["kickoff_time"]
        pred.matchday_date = denorm["matchday_date"]
    else:
        pred = MarketFlowPrediction(
            match_id=match_id,
            odds_snapshot_id=odds_snapshot_id,
            model_version=model_version,
            created_at=now,
            home_style_tag=home_style_tag,
            away_style_tag=away_style_tag,
            best_total_goals=result.get("best_total_goals"),
            second_total_goals=result.get("second_total_goals"),
            best_score=result.get("best_score"),
            second_score=result.get("second_score"),
            trace_json=result.get("trace"),
            league_id=denorm["league_id"],
            kickoff_time=denorm["kickoff_time"],
            matchday_date=denorm["matchday_date"],
        )
        db.add(pred)

    try:
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        return _err("failed to write marketflow prediction", 500)

    return {"data": {
        "best_total_goals": result.get("best_total_goals"),
        "second_total_goals": result.get("second_total_goals"),
        "best_score": result.get("best_score"),
        "second_score": result.get("second_score"),
        "trace": result.get("trace"),
    }}


@router.post("/backtest")
async def backtest_market_flow(payload: dict = Body(...), db: AsyncSession = Depends(get_db)):
    start_kickoff = _parse_dt(payload.get("start_kickoff"))
    end_kickoff = _parse_dt(payload.get("end_kickoff"))
    if not start_kickoff or not end_kickoff:
        return _err("start_kickoff/end_kickoff must be ISO string", 400)
    if start_kickoff >= end_kickoff:
        return _err("start_kickoff must be before end_kickoff", 400)

    engine_version = payload.get("engine_version") or "v2"
    if not isinstance(engine_version, str) or engine_version not in {"v1", "v2"}:
        return _err("engine_version must be v1 or v2", 400)

    config = payload.get("config")
    if engine_version == "v2" and config is not None and not isinstance(config, dict):
        return _err("config must be object", 400)

    source = payload.get("source")
    if source is not None and (not isinstance(source, str) or not source):
        return _err("source must be string", 400)

    overwrite = payload.get("overwrite", False)
    if not isinstance(overwrite, bool):
        return _err("overwrite must be bool", 400)

    model_version = payload.get("model_version")
    if model_version is None:
        model_version = "marketflow_v1" if engine_version == "v1" else "marketflow_v2"
    if not isinstance(model_version, str) or not model_version:
        return _err("model_version must be string", 400)

    match_result = await db.execute(
        select(Match)
        .where(Match.kickoff_time >= start_kickoff, Match.kickoff_time <= end_kickoff)
        .order_by(Match.kickoff_time.asc())
    )
    matches = match_result.scalars().all()

    total = len(matches)
    predicted = 0
    skipped = 0
    skipped_detail: list[dict] = []

    goals_hit_best = 0
    goals_hit_top2 = 0
    score_hit_best = 0
    score_hit_top2 = 0

    if engine_version == "v1":
        engine: MarketFlowEngine | MarketFlowEngineV2 = MarketFlowEngine()
    else:
        engine = MarketFlowEngineV2()

    for match in matches:
        snap_stmt = select(JczqPlayOddsSnapshot).where(JczqPlayOddsSnapshot.match_id == match.id)
        if source:
            snap_stmt = snap_stmt.where(JczqPlayOddsSnapshot.source == source)
        snap_stmt = snap_stmt.order_by(JczqPlayOddsSnapshot.snapshot_time.desc()).limit(1)
        snap_result = await db.execute(snap_stmt)
        snap = snap_result.scalar_one_or_none()
        if not snap:
            skipped += 1
            skipped_detail.append({"match_id": match.id, "reason": "missing_odds_snapshot"})
            continue

        pred_result = await db.execute(select(MarketFlowPrediction).where(MarketFlowPrediction.match_id == match.id))
        pred = pred_result.scalar_one_or_none()

        if pred and not overwrite:
            skipped += 1
            skipped_detail.append({"match_id": match.id, "reason": "prediction_exists"})
        else:
            home_style_tag = "均衡"
            away_style_tag = "均衡"

            if match.home_team_id:
                home_team_result = await db.execute(select(Team).where(Team.id == match.home_team_id))
                home_team = home_team_result.scalar_one_or_none()
                if home_team and home_team.style_tag:
                    home_style_tag = home_team.style_tag

            if match.away_team_id:
                away_team_result = await db.execute(select(Team).where(Team.id == match.away_team_id))
                away_team = away_team_result.scalar_one_or_none()
                if away_team and away_team.style_tag:
                    away_style_tag = away_team.style_tag

            if engine_version == "v1":
                result = engine.predict(
                    home_style_tag=home_style_tag,
                    away_style_tag=away_style_tag,
                    had={"home": snap.had_home, "draw": snap.had_draw, "away": snap.had_away},
                    hhad={
                        "line": snap.hhad_line,
                        "home": snap.hhad_home,
                        "draw": snap.hhad_draw,
                        "away": snap.hhad_away,
                    },
                    ttg=snap.ttg_odds_json or {},
                    crs=snap.crs_odds_json or {},
                )
            else:
                result = engine.predict(
                    home_style_tag=home_style_tag,
                    away_style_tag=away_style_tag,
                    had={"home": snap.had_home, "draw": snap.had_draw, "away": snap.had_away},
                    hhad={
                        "line": snap.hhad_line,
                        "home": snap.hhad_home,
                        "draw": snap.hhad_draw,
                        "away": snap.hhad_away,
                    },
                    ttg=snap.ttg_odds_json or {},
                    crs=snap.crs_odds_json or {},
                    config=config,
                )
            ok, missing = _validate_engine_result(result)
            if not ok:
                skipped += 1
                skipped_detail.append({
                    "match_id": match.id,
                    "reason": "engine_result_incomplete",
                    "missing": missing,
                })
                continue

            # 查弱联赛名称（TOP1弱联赛惩罚用）
            _league_name = None
            if match.league_id:
                _lg_res = await db.execute(select(League.name_zh).where(League.id == match.league_id))
                _league_name = _lg_res.scalar_one_or_none()

            result = _apply_v3e_postprocess(result, snap, league_name_zh=_league_name, matchday_date=getattr(match, "matchday_date", None))

            now = datetime.utcnow()
            denorm = _mk_mfp_denorm(match)
            if pred:
                pred.odds_snapshot_id = snap.id
                pred.model_version = model_version
                pred.created_at = now
                pred.home_style_tag = home_style_tag
                pred.away_style_tag = away_style_tag
                pred.best_total_goals = result.get("best_total_goals")
                pred.second_total_goals = result.get("second_total_goals")
                pred.best_score = result.get("best_score")
                pred.second_score = result.get("second_score")
                pred.trace_json = result.get("trace")
                pred.league_id = denorm["league_id"]
                pred.kickoff_time = denorm["kickoff_time"]
                pred.matchday_date = denorm["matchday_date"]
            else:
                pred = MarketFlowPrediction(
                    match_id=match.id,
                    odds_snapshot_id=snap.id,
                    model_version=model_version,
                    created_at=now,
                    home_style_tag=home_style_tag,
                    away_style_tag=away_style_tag,
                    best_total_goals=result.get("best_total_goals"),
                    second_total_goals=result.get("second_total_goals"),
                    best_score=result.get("best_score"),
                    second_score=result.get("second_score"),
                    trace_json=result.get("trace"),
                    league_id=denorm["league_id"],
                    kickoff_time=denorm["kickoff_time"],
                    matchday_date=denorm["matchday_date"],
                )
                db.add(pred)
            predicted += 1

        if match.home_score is None or match.away_score is None or not pred:
            continue

        actual_total_goals = int(match.home_score) + int(match.away_score)
        actual_score = f"{int(match.home_score)}-{int(match.away_score)}"

        best_total_goals = pred.best_total_goals
        second_total_goals = pred.second_total_goals
        if isinstance(best_total_goals, int):
            if actual_total_goals == best_total_goals:
                goals_hit_best += 1
            if actual_total_goals in {best_total_goals, second_total_goals}:
                goals_hit_top2 += 1

        best_score = _norm_score(pred.best_score)
        second_score = _norm_score(pred.second_score)
        if best_score:
            if actual_score == best_score:
                score_hit_best += 1
            if actual_score in {best_score, second_score}:
                score_hit_top2 += 1

    try:
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        return _err("failed to write marketflow prediction", 500)

    return {"data": {
        "total": total,
        "predicted": predicted,
        "skipped": skipped,
        "goals_hit_best": goals_hit_best,
        "goals_hit_top2": goals_hit_top2,
        "score_hit_best": score_hit_best,
        "score_hit_top2": score_hit_top2,
        "skipped_detail": skipped_detail,
    }}


def _today_window_for_view():
    from datetime import timedelta as _td
    # “当日” 用的是竞彩开售习惯：当日 12:00 ~ 次日 11:59 窗口（跟 _run_tuned7_0821 一致）
    now = datetime.now()
    start = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if start > now:
        start = start - _td(days=1)
    end = start + _td(days=7)
    return start, end


def _score_outcome(h, a):
    if not (isinstance(h, int) and isinstance(a, int)):
        return None
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def _flatten_top3_from_trace(trace):
    top: list[str] = []
    seen: set[str] = set()
    stage_f = (trace or {}).get("stage_f") or {}
    for key in ("best", "second"):
        meta = stage_f.get(key) or {}
        if isinstance(meta, dict):
            candidates = meta.get("candidates") or []
            if isinstance(candidates, list):
                for c in candidates:
                    if not isinstance(c, dict):
                        continue
                    s = c.get("score")
                    if isinstance(s, str) and s and s not in seen:
                        seen.add(s)
                        top.append(s)
                        if len(top) >= 3:
                            return top
    return top


def _all_score_candidates_from_trace(trace):
    """提取 stage_f best+second 里所有候选比分，带 rank_key（用于排序）。返回 [(score_str, rank_key), ...] 去重。"""
    items: list[tuple[str, list]] = []
    seen: set[str] = set()
    stage_f = (trace or {}).get("stage_f") or {}
    for key in ("best", "second"):
        meta = stage_f.get(key) or {}
        if not isinstance(meta, dict):
            continue
        candidates = meta.get("candidates") or []
        if not isinstance(candidates, list):
            continue
        for c in candidates:
            if not isinstance(c, dict):
                continue
            s = c.get("score")
            if not isinstance(s, str) or not s or s in seen:
                continue
            seen.add(s)
            rk = c.get("rank_key") or [0, 0, s]
            if not isinstance(rk, list):
                rk = [0, 0, s]
            items.append((s, list(rk)))
    items.sort(key=lambda x: x[1])
    return items


def _align_preferred_scores(preferred_outcome: str | None,
                            allowed_outcomes: list[str] | None,
                            current_best: str | None,
                            current_second: str | None,
                            current_top3: list[str],
                            trace: dict) -> tuple[str | None, str | None, list[str]]:
    """
    比分 Top2/Top3 选择逻辑（优化版）：
      1) 如果 preferred_outcome 非空 → Top1 永远是 preferred_outcome 方向的最强候选（保持展示一致性）；
      2) 按 allowed_outcomes（赔率阶段的方向候选）铺方向：每个 allowed 方向至少占 1 个 Top3 比分槽位，
         这样 allowed 里只要包含 actual 方向，S3 的候选池里就一定有至少 1 个 actual 方向比分，
         解决 pref=draw 错了导致 S3 全是平局的「单边下注」问题；
      3) Top3 无重复，长度最多 3；score_top2 = top3[0:2]（保证 score_top2 ⊆ score_top3）；
      4) 若 allowed_outcomes 不足 3 个（例如 3 选 1 或 2 选 1），剩余槽位用 preferred 方向的次强候选填充（浓度优化）。
    """
    def _outc(s):
        if not s or "-" not in s:
            return None
        try:
            h, a = s.split("-", 1)
            h, a = int(h), int(a)
        except Exception:
            return None
        if h > a: return "home"
        if h < a: return "away"
        return "draw"

    current_best = _norm_score(current_best)
    current_second = _norm_score(current_second)
    # 构建所有候选比分（按 stage_f 的 rank_key 升序 = 原排序列）
    all_cand = _all_score_candidates_from_trace(trace)
    cand_scores = [s for s, _ in all_cand]
    # 合并当前 top3，放到候选末尾（不影响排序顺序）
    for s in current_top3 + [x for x in (current_best, current_second) if x]:
        if s and s not in cand_scores:
            cand_scores.append(s)

    allowed = list(allowed_outcomes) if allowed_outcomes else []
    # allowed 如果是空（比如 trace 解析失败），兜底 3 方向
    if len(allowed) == 0:
        allowed = ["home", "draw", "away"]
    allowed_set = set(allowed)

    # 每个 allowed 方向，在候选池里的 top1 候选
    per_dir_best: dict[str, str | None] = {}
    per_dir_rest: dict[str, list[str]] = {}
    for dir_ in allowed:
        per_dir_best[dir_] = None
        per_dir_rest[dir_] = []
    for s in cand_scores:
        dir_ = _outc(s)
        if not dir_ or dir_ not in allowed_set:
            continue
        if per_dir_best[dir_] is None:
            per_dir_best[dir_] = s
        elif len(per_dir_rest[dir_]) < 8:
            per_dir_rest[dir_].append(s)

    # 取每个方向的 best 列表（已按 rank_key 排序，因为 cand_scores 本身已 sort）
    # 构造 new_top3
    new_top3: list[str] = []
    added_dirs: set[str] = set()

    # === Head (Top1) 永远是 preferred 方向的最强候选（保持 UI 一致性） ===
    head_score: str | None = None
    if preferred_outcome and preferred_outcome in allowed_set:
        head_score = per_dir_best.get(preferred_outcome) or None
        # 兜底如果 dir_best 没有（理论不应发生，因为之前步骤已经保证 pref 有候选）
        if not head_score:
            # 从 cand_scores 里直接挑第一个 pref 方向的
            for s in cand_scores:
                if _outc(s) == preferred_outcome:
                    head_score = s
                    break
    if not head_score and current_best:
        head_score = current_best
    if head_score and head_score not in new_top3:
        new_top3.append(head_score)
        hd = _outc(head_score)
        if hd:
            added_dirs.add(hd)

    # === 剩余槽位：先填 allowed 里还没加的方向各 1 个 best，再填 pref 次强，最后填剩余 best/second ===
    remaining_allowed = [d for d in allowed if d not in added_dirs]
    for d in remaining_allowed:
        if len(new_top3) >= 3:
            break
        s = per_dir_best.get(d) or None
        if not s or s in new_top3:
            # 从 rest 里找
            rest = per_dir_rest.get(d) or []
            for r in rest:
                if r not in new_top3:
                    s = r
                    break
        if s and s not in new_top3:
            new_top3.append(s)
            added_dirs.add(d)

    # 还有空槽？（当 allowed.size < 3 时）
    if len(new_top3) < 3:
        # 先加 preferred_outcome 方向的次强候选（浓度优化）
        extra_order: list[str] = []
        if preferred_outcome and preferred_outcome in allowed_set:
            extra_order.append(preferred_outcome)
        for d in allowed:
            if d not in extra_order:
                extra_order.append(d)
        for d in extra_order:
            if len(new_top3) >= 3:
                break
            rest = per_dir_rest.get(d) or []
            for r in rest:
                if len(new_top3) >= 3:
                    break
                if r not in new_top3:
                    new_top3.append(r)

    # 最终兜底：还不满 3 的话把原 best/second/current_top3 塞进去
    if len(new_top3) < 3:
        for s in [current_best, current_second] + list(current_top3):
            if len(new_top3) >= 3:
                break
            if s and s not in new_top3:
                new_top3.append(s)

    best_norm = new_top3[0] if len(new_top3) >= 1 else current_best
    second_norm = new_top3[1] if len(new_top3) >= 2 else current_second
    top3_norm = new_top3[:3]
    return best_norm, second_norm, top3_norm


def _fav_from_line(hhad_line) -> str | None:
    """让球方判定：line<0 → 主队让球（fav=home）；line>0 → 客队让球（fav=away）；0/None → 无让球方。"""
    if hhad_line is None:
        return None
    try:
        f = float(hhad_line)
    except (TypeError, ValueError):
        return None
    if f < 0:
        return "home"
    if f > 0:
        return "away"
    return None


def _had_implied_map(snap) -> dict[str, float] | None:
    """HAD 三向归一化隐含概率（让球方绝对命中率的主导变量）。"""
    if snap is None:
        return None
    h = _as_float(getattr(snap, "had_home", None))
    d = _as_float(getattr(snap, "had_draw", None))
    a = _as_float(getattr(snap, "had_away", None))
    if not (h and d and a):
        return None
    inv = 1.0 / h + 1.0 / d + 1.0 / a
    return {"home": (1.0 / h) / inv, "draw": (1.0 / d) / inv, "away": (1.0 / a) / inv}


def _cold_dir(allowed_set: set, fav: str, imp: dict | None) -> str | None:
    """冷门参考方向：二选内非让球方方向，优先平局（冷门多来自平局兜底）。"""
    others = [k for k in allowed_set if k != fav]
    if not others:
        return None
    if "draw" in others:
        return "draw"
    if imp:
        cands = [k for k in others if imp.get(k)]
        if cands:
            return max(cands, key=lambda k: imp[k])
    return others[0]


def _pool_assign_v2(allowed: list[str], snap, league_name_zh: str | None) -> tuple[str, str | None, str | None, float | None, str | None, bool]:
    """四池分配（用户口径：正路=让球方获胜，冷门=让球方未获胜）。

    返回 (pool, pick, fav, fav_ip, cold_dir, cold_signal)：
      pick 为 None 表示该池不做方向优选；cold_dir 为冷门参考方向（警示用，仅冷门池有）。
      favorite  正路池: fav∈二选 且 HAD隐含概率(fav_ip)>=0.58 且无负信号 → pick=fav
      upset     冷门池: fav∉二选 → pick=二选内非fav隐含最高（强冷门，方向优选）
                        或 fav∈二选 且平局隐含概率 draw_ip>=0.30 → 冷门信号转入，
                        pick=None 仅警示（preferred=None），cold_dir=非让球方方向（优先平局）
      ambiguous 模糊池: fav∈二选 且 fav_ip<0.58（或 fav_ip 缺失）→ 不下单选
      unpooled  不入池: 无让球方 或 负信号场次（韩K / HHAD sweet_high / very_low×主让）→ 不做选择

    180 天验证（N=1801）：favorite 400场/66.5% | 强冷门 75场/41.3%
    警示冷门（模糊池 draw_ip>=0.28）N=265 冷门率(act!=fav)=60.0%（前61.6/后56.0 两段稳定），
      每日覆盖 112 天；但参考方向命中率仅 32.1%，故仅警示不主推（preferred=None）。
    负信号场次是"超额偏差为负"而非冷门倾向，绝不能转入冷门池，只能入不入池。
    """
    allowed_set = set(allowed or ["home", "draw", "away"])
    line = None
    if snap is not None:
        _hhl = getattr(snap, "hhad_line", None)
        if isinstance(_hhl, (int, float)) and _hhl == _hhl:  # 排除 NaN；hhad_line 允许 0 与负值
            line = float(_hhl)
    fav = _fav_from_line(line)
    imp = _had_implied_map(snap)
    fav_ip = imp.get(fav) if (imp is not None and fav) else None
    draw_ip = imp.get("draw") if imp is not None else None
    hhad = {}
    if snap is not None:
        _hh = {
            "home": _as_float(getattr(snap, "hhad_home", None)),
            "draw": _as_float(getattr(snap, "hhad_draw", None)),
            "away": _as_float(getattr(snap, "hhad_away", None)),
        }
        if all(v is not None for v in _hh.values()):
            hhad = _hh
    pb = (_hhad_signal_v2(hhad) or {}).get("pref_bucket") if hhad else None
    neg = (
        league_name_zh == "韩K"
        or pb == "sweet_high"
        or (pb == "very_low" and fav == "home")
    )
    if not fav:
        return "unpooled", None, None, None, None, False
    if fav not in allowed_set:
        cands = [k for k in allowed_set if k != fav and imp is not None and imp.get(k)]
        pick = max(cands, key=lambda k: imp[k]) if cands else None
        return "upset", pick, fav, fav_ip, pick, False
    if fav_ip is None:
        return "ambiguous", None, fav, None, None, False
    if fav_ip >= 0.58:
        if neg:
            return "unpooled", None, fav, fav_ip, None, False
        return "favorite", fav, fav, fav_ip, None, False
    # 模糊池内冷门信号：平局隐含概率>=0.28 → 让球方未获胜率 60.0%（两段稳定），转入冷门池仅警示
    if draw_ip is not None and draw_ip >= 0.28:
        cold_dir = _cold_dir(allowed_set, fav, imp)
        return "upset", None, fav, fav_ip, cold_dir, True
    return "ambiguous", None, fav, fav_ip, None, False


def _compute_preferred_outcome(
    trace: dict,
    snap,
    league_name_zh: str | None = None,
    matchday_date = None,
) -> tuple[str | None, dict[str, bool]]:
    """统一计算『最终 preferred_outcome + 触发的桶标志』。

    新桶结构 V4（取代原 V3e 单桶的 AND 三维窄门）：
      - V3e：保留原 [1.74,2.0) × 一球盘 × 含 D 二选（净收益 0，留作 display 兼容锚点 + 标记 v3e_draw_bucket）
      - V3g_B：一球盘 × {A,H}（D 被 enforce 踢）× 赔率 [2.35,2.80) × let=H had_pref=H hhad_pref=A（反向冲突）× D赔率<3.20
              → 加回 D，pref=draw（144 场模拟：n=4 save=3 kill=1 → NET_PREF=+2, NET_DIR=+4）
      - V3g_C：一球盘 × {A,H}（D 被 enforce 踢）× D赔率>=3.80 × 让球盘 hhad 与 had_pref 反向
              → 加回 D，pref=draw（144 场模拟：n=1 save=1 kill=0 → NET +1）
      - 其他桶（V3f_B [1.55,1.74)、V3g_A [2.0,2.15)、V3h [1.40,1.55)、V3e 扩围）均净负，停用
    返回: (preferred_outcome, bucket_triggers)
    bucket_triggers: dict[str, bool] 包含 v3e_draw_bucket / v3g_bucket / v3h_bucket / v3f_bucket 等
    """
    if not isinstance(trace, dict):
        return None, {}
    stage_b = trace.get("stage_b") or {}
    allowed = stage_b.get("allowed_outcomes") or ["home", "draw", "away"]
    had_pref = stage_b.get("had_pref")
    outcome_votes = stage_b.get("outcome_votes") or {}

    # ===== 路线1/2用的赛季月份（来自matchday_date参数） =====
    md_month: int | None = None
    try:
        if matchday_date is not None and hasattr(matchday_date, "month"):
            md_month = int(matchday_date.month)
        elif isinstance(matchday_date, str) and len(matchday_date) >= 7:
            md_month = int(matchday_date[5:7])
    except Exception:
        md_month = None
    ROUTE1_WEAK_TOP3_LEAGUES: set[str] = {"芬超", "日职联", "瑞典超", "法甲"}  # TOP3×主让一球内 命中<40%的4联赛（共207场）
    ROUTE2_SEASON_A_MONTHS: set[int] = {3,4,5,9,10}  # 常规赛季月，TOP3/TOP2洼地
    POS_ROUTE_LEAGUES: set[str] = {"芬超","英冠","德甲","日职联","瑞典超","挪超"}  # 路线1/2正向白名单6联赛（探针净值+19）
    NEG_ROUTE_LEAGUES: set[str] = {"西甲","英超","意甲","法甲","韩K"}  # 路线1/2负向黑名单5联赛（探针净值-14，完全排除）

    preferred_outcome: str | None = None
    bucket_triggers: dict[str, bool] = {}
    _allowed_set = set(allowed)
    snap_odds_map = {}
    hhad_h = None
    hhad_a = None
    hhad_line_f = None
    if snap is not None:
        for dir_, col in (("home", "had_home"), ("draw", "had_draw"), ("away", "had_away")):
            v = getattr(snap, col, None)
            if isinstance(v, (int, float)) and v > 1:
                snap_odds_map[dir_] = float(v)
        _hhl = getattr(snap, "hhad_line", None)
        if isinstance(_hhl, (int, float)) and _hhl == _hhl:
            hhad_line_f = float(_hhl)
        for attr, dest in (("hhad_home", "hhad_h"), ("hhad_away", "hhad_a")):
            v = getattr(snap, attr, None)
            if isinstance(v, (int, float)) and v > 1:
                if dest == "hhad_h":
                    hhad_h = float(v)
                else:
                    hhad_a = float(v)
    odds_usable = bool(
        len(snap_odds_map) >= 2
        and all(o in snap_odds_map for o in _allowed_set if o in ("home", "draw", "away"))
    )
    is_one_ball = (
        hhad_line_f is not None and 0.75 <= abs(hhad_line_f) <= 1.25
    )
    # ===== 保存一份**未被TOP1惩罚污染的原始H/A赔率**，供路线1/2判断赔率桶用 =====
    #   关键bug修复：之前L885/L891条件用had_pref_odds=选pref方向赔率可能=away赔率≠home赔率，
    #   而路线1/2触发条件：当home赔率[2.0,2.35)=TOP3且主让一球+弱联赛→对H乘惩罚；
    #   必须用原始home赔率判断桶归属，不能用乘惩罚后的值，也不能用had_pref=away方向的赔率。
    raw_home_odds: float | None = None
    raw_away_odds: float | None = None
    raw_draw_odds: float | None = None
    if snap is not None:
        for attr, dest in (("had_home", "raw_home_odds"), ("had_draw", "raw_draw_odds"), ("had_away", "raw_away_odds")):
            v = getattr(snap, attr, None)
            if isinstance(v, (int, float)) and v > 1:
                if dest == "raw_home_odds":
                    raw_home_odds = float(v)
                elif dest == "raw_away_odds":
                    raw_away_odds = float(v)
                else:
                    raw_draw_odds = float(v)
    had_pref_odds = snap_odds_map.get(had_pref) if isinstance(had_pref, str) and had_pref in snap_odds_map else None
    draw_odds = snap_odds_map.get("draw")
    hhad_pref = None
    if hhad_h is not None and hhad_a is not None:
        hhad_pref = "home" if hhad_h <= hhad_a else "away"
    # ===== TOP1 弱联赛5桶 分联赛×分方向精细赔率惩罚 =====
    # 事实基础（N=7096 2025-01-01~2026-08-14）：
    # 英冠n=188 40.96% (-11.2pp) | H/A双差: H=42.1%/A=38.3% D真实平局率28.2% 偏高
    # 法乙n=98 40.82% (-11.4pp) | H极差37.3% / A=48.4%(强 / D真实=37.8%全场最高（模型从不选D）
    # 荷甲n=154 50.00% (-2.2pp) | A=56.36%远强于全局 / H=46.46%弱
    # 德乙n=105 46.67% (-5.5pp) | A=48.48%好 / H=45.83%弱
    # 日乙n=67 47.76% (-4.4pp) | A=51.85%好 / H=45.00%弱
    # TOP1 V3（纯赔率惩罚，不改anchors二选约束）：H惩罚×1.30以上确保压过away×1.05赔率；法乙D×0.92回调避免过度引导低命中D
    WEAK_LEAGUE_PENALTIES = {
        "法乙": (1.32, 0.92, 1.00),
        "英冠": (1.30, 0.95, 1.00),
        "荷甲": (1.32, 1.00, 1.00),
        "德乙": (1.32, 1.00, 1.00),
        "日乙": (1.25, 1.00, 1.00),
    }
    if isinstance(league_name_zh, str) and league_name_zh in WEAK_LEAGUE_PENALTIES:
        bucket_triggers["weak_league_bucket"] = True
        h_m, d_m, a_m = WEAK_LEAGUE_PENALTIES[league_name_zh]
        if h_m != 1.00 and "home" in snap_odds_map:
            snap_odds_map["home"] = float(snap_odds_map["home"]) * h_m
        if d_m != 1.00 and "draw" in snap_odds_map:
            snap_odds_map["draw"] = float(snap_odds_map["draw"]) * d_m
        if a_m != 1.00 and "away" in snap_odds_map:
            snap_odds_map["away"] = float(snap_odds_map["away"]) * a_m
        # 重新取新值
        had_pref_odds = snap_odds_map.get(had_pref) if isinstance(had_pref, str) and had_pref in snap_odds_map else None
        draw_odds = snap_odds_map.get("draw")
    # ===== TOP3 毒药桶直接强制 draw【临时停用，架构判断错误】=====
    # 事实数据：TOP3桶真实平局率=27.3%（459/1683），直接强制pref=draw命中率≈28%，反而比原选H/A的41.47%低13.5pp！
    # 1128/7096=16%样本强制设28%命中率的D→全局p_hit暴跌-2.09pp（52.16→50.07）。正确方向后续应该在H/A二选内做调权，不能强制D。
    # if (not preferred_outcome) and is_one_ball and odds_usable:
    #     if (
    #         isinstance(had_pref, str)
    #         and had_pref in ("home", "away")
    #         and had_pref_odds is not None
    #         and 2.00 <= had_pref_odds < 2.35
    #         and draw_odds is not None
    #         and 2.90 <= draw_odds <= 3.40
    #     ):
    #         preferred_outcome = "draw"
    #         bucket_triggers["v3m_top3_draw_bucket"] = True
    # ===== V3g_B（已停用，事实数据：[2.35,2.80) 一球盘反向冲突 D<3.20 触发的 draw p_hit≈38% 反而拖后腿 -13pp）
    # if is_one_ball and odds_usable:
    #     if (
    #         _allowed_set == {"away", "home"}
    #         and had_pref_odds is not None
    #         and 2.35 <= had_pref_odds < 2.80
    #         and had_pref == "home"
    #         and hhad_pref == "away"
    #         and hhad_line_f is not None
    #         and hhad_line_f < 0
    #         and draw_odds is not None
    #         and draw_odds < 3.20
    #     ):
    #         preferred_outcome = "draw"
    #         bucket_triggers["v3g_bucket"] = True
    # ===== V3g_C（已停用，事实数据：D≥3.80 反向触发 draw net 净负，拖后 preferred_outcome=draw 命中率）
    # if (not preferred_outcome) and is_one_ball and odds_usable:
    #     if (
    #         _allowed_set == {"away", "home"}
    #         and draw_odds is not None
    #         and draw_odds >= 3.80
    #         and had_pref in ("home", "away")
    #         and hhad_pref is not None
    #         and hhad_pref != had_pref
    #     ):
    #         preferred_outcome = "draw"
    #         bucket_triggers["v3g_bucket"] = bucket_triggers.get("v3g_bucket", False) or True
    #         bucket_triggers["v3gC_bucket"] = True
    # ===== V3e（已停用，事实数据：一球盘×1.74-2.0×含D二选→draw 1013场 p_hit=30.7% -21.5pp，拖全局）=====
    # if (not preferred_outcome) and is_one_ball and odds_usable:
    #     if _allowed_set == {"home", "draw"} and "home" in snap_odds_map:
    #         _h = snap_odds_map["home"]
    #         if 1.74 <= _h < 2.0:
    #             preferred_outcome = "draw"
    #             bucket_triggers["v3e_draw_bucket"] = True
    #     elif _allowed_set == {"away", "draw"} and "away" in snap_odds_map:
    #         _a = snap_odds_map["away"]
    #         if 1.74 <= _a < 2.0:
    #             preferred_outcome = "draw"
    #             bucket_triggers["v3e_draw_bucket"] = True
    # ===== V3k（已停用，事实数据：keep_two={H,A} 且 [2.00,2.35)×一球盘 n=295 p_hit=32.2% -20pp！
    #   keep_two={H,A} 直接排除了约27%真实平局的命中可能性，纯H/A二选不可能突破 73%×50%=36.5% 天花板。）=====
    # if (not preferred_outcome) and is_one_ball and odds_usable:
    #     if _allowed_set == {"home", "away"} and had_pref in ("home", "away") and had_pref_odds is not None:
    #         if 2.00 <= had_pref_odds < 2.35:
    #             rev = "away" if had_pref == "home" else "home"
    #             if rev in _allowed_set and rev in snap_odds_map:
    #                 preferred_outcome = rev
    #                 bucket_triggers["v3k_reverse_bucket"] = True
    # ===== V2 fallback: 低赔赔率优先（隐含概率最高），tie-break 用 had_pref =====
    # TOP4 away方向惩罚：away系统性弱 p_hit=49.96% vs home=53.49% 差3.5pp，赔率×1.05提高门槛降低选中概率
    if not preferred_outcome and odds_usable:
        _cands = []
        for o in allowed:
            if o not in _allowed_set or o not in snap_odds_map:
                continue
            odds_val = snap_odds_map[o]
            if o == "away":
                odds_val = float(odds_val) * 1.05
            # ===== 路线#1 league维度第三维权重穿透 =====
            # 事实：TOP3[2.00,2.35)×主让一球×{芬超,日职联,瑞典超,法甲}命中<40% vs 强组{意甲/西甲/德甲/韩K}47-48% → 差10pp+
            # 【2026-08-25 bug修】条件判断必须用 raw_home_odds（原始home赔率=未被TOP1惩罚污染），
            #   不能用had_pref_odds（had_pref=away时赔率≠home赔率，会导致条件判断全错0%触发）
            # 【2026-08-25 V2调幅】1.10→1.25：H×1.25=2.0→2.50；away×1.05=2.85→2.99。保证2.0≤raw_H<2.35时 H×1.25=2.50~2.9375，away赔率≈2.70~3.00，能让away胜出。
            # 【2026-08-25 V3联赛分层】仅正向白名单6联赛生效，排除负向5联赛（全联赛版0净贡献→分层版Δ+0.25pp 净值+19）
            if (o == "home" and isinstance(raw_home_odds, (int,float)) and 2.00 <= float(raw_home_odds) < 2.35
                    and hhad_line_f is not None and hhad_line_f > 0 and 0.9 <= hhad_line_f <= 1.1
                    and isinstance(league_name_zh, str) and league_name_zh in ROUTE1_WEAK_TOP3_LEAGUES
                    and league_name_zh in POS_ROUTE_LEAGUES and league_name_zh not in NEG_ROUTE_LEAGUES):
                odds_val = float(odds_val) * 1.25
            # ===== 路线#2 赛季窗口×TOP3/TOP2 双桶穿透 =====
            # 事实：A常规赛季月(3-5/9-10) TOP3/TOP2 p_hit 38.43%/33.95% vs C夏窗 47.43%/43.14% → 各差9pp
            # 【2026-08-25 bug修】同上，用 raw_home_odds 而不是 had_pref_odds（避免had_pref=away全0触发）
            # 【2026-08-25 V2调幅】1.06→1.15：H×1.15=2.0→2.30；away×1.05=2.80~3.30→away赔率显著低于H×1.15
            # 【2026-08-25 V3联赛分层】同上，仅正向白名单6联赛生效
            if (o == "home" and isinstance(raw_home_odds, (int,float)) and 2.00 <= float(raw_home_odds) < 2.80
                    and md_month is not None and md_month in ROUTE2_SEASON_A_MONTHS
                    and isinstance(league_name_zh, str) and league_name_zh in POS_ROUTE_LEAGUES
                    and league_name_zh not in NEG_ROUTE_LEAGUES):
                odds_val = float(odds_val) * 1.15
            tiebreak_had = 0 if o == had_pref else 1
            _cands.append((o, odds_val, tiebreak_had))
        if _cands:
            _cands.sort(key=lambda x: (x[1], x[2]))
            preferred_outcome = _cands[0][0]
    # ===== 兜底原 A1 (赔率不可用) =====
    if not preferred_outcome and isinstance(outcome_votes, dict) and len(outcome_votes) > 0:
        _candidates = []
        for o in allowed:
            if o not in _allowed_set:
                continue
            v = int(outcome_votes.get(o) or 0)
            if o == "draw" and had_pref != "draw":
                v_adj = int(v * 0.70)
            else:
                v_adj = v
            tiebreak_had = 0 if o == had_pref else 1
            draw_tiebreak = 1 if o == "draw" else 0
            _candidates.append((o, v_adj, tiebreak_had, draw_tiebreak, v))
        if _candidates:
            _candidates.sort(key=lambda x: (-x[1], x[2], x[3]))
            preferred_outcome = _candidates[0][0]
    if not preferred_outcome and len(allowed) > 0:
        preferred_outcome = allowed[0]
    return preferred_outcome, bucket_triggers


def _ensure_allowed_outcomes(trace: dict, bucket_triggers: dict[str, bool]) -> list[str]:
    """当 V3g_B / V3g_C / V3m_TOP3 桶触发时，将被 enforce 剔除的 draw 加回 allowed_outcomes。

    返回最终允许方向 list（已写入 stage_b，就地修改 trace）。
    """
    stage_b = trace.setdefault("stage_b", {})
    if not isinstance(stage_b, dict):
        stage_b = {}
        trace["stage_b"] = stage_b
    allowed_raw = stage_b.get("allowed_outcomes") or ["home", "draw", "away"]
    allowed_set = set(allowed_raw)
    v3g_triggered = bool(bucket_triggers.get("v3g_bucket"))
    v3m_triggered = bool(bucket_triggers.get("v3m_top3_draw_bucket"))
    need_draw_back = v3g_triggered or v3m_triggered
    _reason = "v3g_bucket_unexclude_draw" if v3g_triggered else ("v3m_top3_unexclude_draw" if v3m_triggered else None)
    _ts = "runtime_v3g_unexclude" if v3g_triggered else ("runtime_v3m_unexclude" if v3m_triggered else None)
    if need_draw_back and _reason and _ts:
        if "draw" not in allowed_set:
            allowed_set.add("draw")
            new_list = sorted(list(allowed_set))
            stage_b["allowed_outcomes"] = new_list
            exc = stage_b.get("excluded_outcomes") or []
            if isinstance(exc, list) and "draw" in exc:
                exc = [x for x in exc if x != "draw"]
                stage_b["excluded_outcomes"] = exc
            enforce_list = stage_b.get("enforce") or []
            if isinstance(enforce_list, list):
                enforce_list.append({
                    "kind": "enforce_revert",
                    "reason": _reason,
                    "target_outcome": "draw",
                    "side": "add_back",
                    "timestamp": _ts,
                })
                stage_b["enforce"] = enforce_list
    return sorted(list(allowed_set))


def _apply_v3e_postprocess(result: dict, snap, league_name_zh: str | None = None, matchday_date=None) -> dict:
    """engine 算完 raw result 后，写入 DB 前调用：

    1. V3g_B / V3g_C 触发时把 draw 加回 allowed
    2. 用 _compute_preferred_outcome 算最终 pref（V4 桶结构 + V2 低赔 + fallback）
    3. 把结论写进 trace_json（顶层 + stage_f + stage_b.meta 各桶标志）
    4. 按最终 pref 对齐 best_score / second_score
    返回修改过的 result，用于 persist。
    """
    if not isinstance(result, dict):
        return result
    trace = result.get("trace") or {}
    if not isinstance(trace, dict):
        trace = {}
    stage_b = trace.setdefault("stage_b", {})
    if not isinstance(stage_b, dict):
        stage_b = {}
        trace["stage_b"] = stage_b
    meta = stage_b.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        stage_b["meta"] = meta
    stage_f = trace.setdefault("stage_f", {})
    if not isinstance(stage_f, dict):
        stage_f = {}
        trace["stage_f"] = stage_f

    pref, bucket_triggers = _compute_preferred_outcome(trace, snap, league_name_zh=league_name_zh, matchday_date=matchday_date)
    # V3g 桶触发时，先把被 enforce 剔除的 draw 加回 allowed，再把 best_score/second_score 对齐
    allowed = _ensure_allowed_outcomes(trace, bucket_triggers)

    for k in (
        "v3e_draw_bucket",
        "v3g_bucket",
        "v3gC_bucket",
        "v3h_bucket",
        "v3f_bucket",
        "v3k_reverse_bucket",
        "v3m_top3_draw_bucket",
        "weak_league_bucket",
    ):
        meta[k] = bool(bucket_triggers.get(k))
    if isinstance(pref, str):
        trace["preferred_outcome"] = pref
        stage_f["preferred_outcome"] = pref
        # 兼容旧字段名：v3e_draw_bucket 顶层（UI 已有代码依赖）
        if bucket_triggers.get("v3e_draw_bucket"):
            trace["v3e_draw_bucket"] = True

        best_raw = result.get("best_score") if isinstance(result.get("best_score"), str) else None
        second_raw = result.get("second_score") if isinstance(result.get("second_score"), str) else None
        top3_raw = _flatten_top3_from_trace(trace)
        best_norm, second_norm, _ = _align_preferred_scores(
            preferred_outcome=pref,
            allowed_outcomes=allowed,
            current_best=best_raw,
            current_second=second_raw,
            current_top3=top3_raw,
            trace=trace,
        )
        if isinstance(best_norm, str) and best_norm:
            result["best_score"] = best_norm
        if isinstance(second_norm, str) and second_norm:
            result["second_score"] = second_norm
    result["trace"] = trace
    return result


def _resolve_model_version(model_version):
    if isinstance(model_version, str) and model_version.endswith("_cfusion"):
        return (model_version[: -len("_cfusion")], True, model_version)
    return (model_version, False, (model_version or ""))


async def _default_model_version(db) -> list[str]:
    """返回 DB 中所有 marketflow_v2_* MV（不区分 tuned7 vs tuned7_0821），
    以便 history 展示时把『同一算法家族的不同产出批次』合并展示。

    注意：这里只改 **WHERE 过滤**，不改变任何一行 MarketFlowPrediction 的
    model_version / trace_json 存储（不改算法口径的持久化）。
    preferred_outcome 仍然是 _market_flow_query runtime 按新代码 (V2+V3e) 重算
    用于 display，不会回写到 DB。
    """
    from sqlalchemy import func
    stmt = (
        select(MarketFlowPrediction.model_version)
        .join(Match, Match.id == MarketFlowPrediction.match_id)
        .where(
            MarketFlowPrediction.model_version.like("marketflow_v2_%"),
            MarketFlowPrediction.model_version.notlike("%_cfusion"),
        )
        .group_by(MarketFlowPrediction.model_version)
        .order_by(func.max(Match.kickoff_time).desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    base_list = list(rows) if rows else ["marketflow_v2_tuned7_0821"]
    return [f"{mv}_cfusion" if not mv.endswith("_cfusion") else mv for mv in base_list]


def _c_ttg_top3(expected_goals_c, snap_top2_c):
    base = []
    if isinstance(snap_top2_c, list):
        base = [int(x) for x in snap_top2_c if isinstance(x, (int, float))]
    if len(base) < 2 and isinstance(expected_goals_c, (int, float)) and expected_goals_c > 0:
        for x in snap_top2(float(expected_goals_c)):
            if x not in base:
                base.append(x)
    if len(base) < 3 and isinstance(expected_goals_c, (int, float)) and expected_goals_c > 0:
        r = round(float(expected_goals_c))
        for delta in (0, -1, 1, -2, 2, -3, 3):
            v = r + delta
            if v >= 0 and v not in base:
                base.append(v)
            if len(base) >= 3:
                break
    return base[:3]


async def _market_flow_query(db, *, start=None, end=None, model_version=None, source=None, ou_tier=DEFAULT_TIER, ou_sm_tier=OU_M_DEFAULT):
    """history 查询：默认合并『同一算法家族所有批次 MV』（tuned7 + tuned7_0821 ...）。

    如果调用方显式传了 model_version = 单一值，则按单一 MV 过滤（兼容旧行为）。
    合并策略：同一 match_id + 同算法家族有多条 MFP 时，只保留 id 最大的那条，避免重复。
    存储绝对不动，runtime 重算 preferred_outcome 用于 display。

    返回 (items, ou_stats, ou_sm_stats)：
      items        — 预测列表（含大小球方向 ou_direction[TTG]、ou_sm[SportMonks O/U]）
      ou_stats     — {tier: {signal, bet, skip, settled, hit}}，TTG 各档位命中率
      ou_sm_stats  — {tier: {signal, bet, skip, settled, hit}}，O/U 各档位命中率
    """
    if model_version is None:
        mv_candidates = await _default_model_version(db)  # list[str] 都带 _cfusion
        single_mode = False
    else:
        if not (isinstance(model_version, str) and model_version.startswith("marketflow_v2_")):
            mv_candidates = await _default_model_version(db)
            single_mode = False
        else:
            if not model_version.endswith("_cfusion"):
                model_version = f"{model_version}_cfusion"
            mv_candidates = [model_version]
            single_mode = True

    # 都带 _cfusion，拆成 db 层实际匹配的裸 mv 列表
    db_mvs = []
    for mc in mv_candidates:
        db_mv, _, _ = _resolve_model_version(mc)
        if db_mv and db_mv not in db_mvs:
            db_mvs.append(db_mv)
    label_mv = mv_candidates[0] if len(mv_candidates) == 1 else None  # 合并就不写单一 label
    HomeTeam = aliased(Team, name="home_team")
    AwayTeam = aliased(Team, name="away_team")
    # 永远 LEFT JOIN Prediction（单分支，不存在 is_cfusion=False 的 stmt 歧义路径 —— 防止切换崩端）
    stmt = (
        select(MarketFlowPrediction, Match, JczqPlayOddsSnapshot, HomeTeam, AwayTeam, Prediction, League)
        .join(Match, Match.id == MarketFlowPrediction.match_id)
        .outerjoin(JczqPlayOddsSnapshot, JczqPlayOddsSnapshot.id == MarketFlowPrediction.odds_snapshot_id)
        .outerjoin(HomeTeam, HomeTeam.id == Match.home_team_id)
        .outerjoin(AwayTeam, AwayTeam.id == Match.away_team_id)
        .outerjoin(Prediction, Prediction.match_id == Match.id)
        .outerjoin(League, League.id == Match.league_id)
    )
    if start is not None:
        stmt = stmt.where(Match.kickoff_time >= start)
    if end is not None:
        stmt = stmt.where(Match.kickoff_time <= end)
    stmt = stmt.where(MarketFlowPrediction.model_version.in_(db_mvs))
    stmt = stmt.order_by(Match.kickoff_time.asc(), Match.id.asc(), MarketFlowPrediction.id.desc())
    rows = (await db.execute(stmt)).all()
    # ---- 合并去重：同一 match_id 只取第一条（id DESC 所以是最新一条）
    dedup: dict[int, tuple] = {}
    for row in rows:
        pred = row[0]
        if pred is None:
            continue
        mid = int(getattr(pred, "match_id") or 0)
        if mid in dedup:
            continue
        dedup[mid] = row
    rows = list(dedup.values())
    items = []
    ou_stats = {t: {"signal": 0, "bet": 0, "skip": 0, "settled": 0, "hit": 0} for t in TIERS}
    ou_sm_stats = {t: {"signal": 0, "bet": 0, "skip": 0, "settled": 0, "hit": 0} for t in OU_M_TIERS}

    # 批量取每场 SportMonks O/U 2.5线行（快照升序传入 → ou_market_from_rows 取最新快照）
    ou_map: dict[int, list] = {}
    if rows:
        mids = [int(row[0].match_id) for row in rows]
        ou_res = await db.execute(
            select(
                OddsSnapshot.match_id, OddsSnapshot.bookmaker, OddsSnapshot.goal_line,
                OddsSnapshot.over_odds, OddsSnapshot.under_odds,
            )
            .where(
                OddsSnapshot.match_id.in_(mids),
                OddsSnapshot.goal_line.is_not(None),
                OddsSnapshot.over_odds.is_not(None),
                OddsSnapshot.under_odds.is_not(None),
            )
            .order_by(OddsSnapshot.match_id.asc(), OddsSnapshot.snapshot_time.asc())
        )
        for r in ou_res:
            ou_map.setdefault(r.match_id, []).append(
                (r.bookmaker, r.goal_line, r.over_odds, r.under_odds)
            )

    for row in rows:
        # 单分支解包（统一结构，不再 if/else）
        (pred, match, snap, home, away, pred_c, league) = (row[0], row[1], row[2], row[3], row[4], row[5], row[6])
        is_cfusion = True  # 强制 fused
        home_name = None
        away_name = None
        if home:
            home_name = getattr(home, "name_zh", None) or getattr(home, "name", None) or None
        if away:
            away_name = getattr(away, "name_zh", None) or getattr(away, "name", None) or None
        if not home_name:
            home_name = getattr(match, "home_team_name", None) or "主队"
        if not away_name:
            away_name = getattr(match, "away_team_name", None) or "客队"
        league_name = None
        if league:
            league_name = getattr(league, "name_zh", None) or getattr(league, "name_en", None)
        if not league_name:
            league_name = getattr(match, "league_name", None)
        trace = (pred.trace_json or {}) if isinstance(pred.trace_json, dict) else {}
        # 兼容双层结构：历史批次（180天窗口脚本）把 stage_b 写在内层 {"trace": {...}}，
        # 而 admin 管线写的 stage_b 在顶层。统一剥一层，保证 allowed_outcomes 读得到，避免 fallback 三选。
        if isinstance(trace.get("trace"), dict):
            trace = trace["trace"]
        stage_b = trace.get("stage_b") or {}
        allowed = stage_b.get("allowed_outcomes") or ["home", "draw", "away"]
        had_pref = stage_b.get("had_pref")
        excluded = [x for x in ("home", "draw", "away") if x not in set(allowed)]
        enforce = stage_b.get("enforce") or []
        outcome_votes = stage_b.get("outcome_votes") or {}
        score_top3_raw = _flatten_top3_from_trace(trace)

        # ========== preferred_outcome：先读持久化，再 fallback 统一重算 ==========
        # 新数据（V4 桶持久化后）trace 顶层有 preferred_outcome，直接用 = 持久化结果
        # 老数据没有该字段或 V3g 需要 runtime 加回 D，走 fallback runtime 重算
        runtime_bucket_triggers: dict[str, bool] = {}
        _league_nz = getattr(league, "name_zh", None) if league else None
        if (
            isinstance(trace, dict)
            and isinstance(trace.get("preferred_outcome"), str)
            and trace["preferred_outcome"] in {"home", "draw", "away"}
        ):
            preferred_outcome = trace["preferred_outcome"]
        else:
            preferred_outcome, runtime_bucket_triggers = _compute_preferred_outcome(trace, snap, league_name_zh=_league_nz, matchday_date=getattr(pred, "matchday_date", None))
            # V3g 桶 runtime fallback：允许方向同步把 D 加回，避免 UI 出现 pref=draw 但 allowed 不含 D 的冲突
            if runtime_bucket_triggers.get("v3g_bucket"):
                _allowed_set = set(allowed)
                if "draw" not in _allowed_set:
                    _allowed_set.add("draw")
                    allowed = sorted(list(_allowed_set))

        # ===== 池化分类（用户口径：正路=让球方获胜，冷门=让球方未获胜）=====
        # 四池：favorite 正路池 / upset 冷门池 / ambiguous 模糊池 / unpooled 不入池
        # 正路池、冷门池覆盖 preferred 为池优选方向；模糊池、不入池不做方向选择（preferred=None）
        pool_key, pool_pick, pool_fav, pool_fav_ip, pool_cold_dir, pool_cold_signal = _pool_assign_v2(allowed, snap, _league_nz)
        if pool_key in ("favorite", "upset") and not pool_cold_signal:
            if pool_pick in set(allowed):
                preferred_outcome = pool_pick
        else:
            # 警示冷门（cold_signal=True）仅展示冷门参考方向 cold_dir，不做方向优选
            preferred_outcome = None

        # 用 preferred_outcome 对齐比分推荐
        best_score_norm, second_score_norm, score_top3_norm = _align_preferred_scores(
            preferred_outcome=preferred_outcome,
            allowed_outcomes=allowed,
            current_best=getattr(pred, "best_score", None),
            current_second=getattr(pred, "second_score", None),
            current_top3=score_top3_raw,
            trace=trace,
        )

        actual = None
        actual_score = None
        if isinstance(match.home_score, int) and isinstance(match.away_score, int):
            actual = _score_outcome(match.home_score, match.away_score)
            actual_score = f"{match.home_score}-{match.away_score}"
        best_tg = pred.best_total_goals
        second_tg = pred.second_total_goals
        third_tg = None
        fused_meta = None
        if is_cfusion and pred_c is not None:
            eg_c = getattr(pred_c, "expected_goals_c", None)
            snap2_c = getattr(pred_c, "snap_top2_c", None)
            gd_c = getattr(pred_c, "goal_distribution", None)
            c_top3 = _c_ttg_top3(eg_c, snap2_c)
            if len(c_top3) >= 1:
                best_tg = c_top3[0]
                if len(c_top3) >= 2:
                    second_tg = c_top3[1]
                if len(c_top3) >= 3:
                    third_tg = c_top3[2]
                fused_meta = {
                    "source": "model_c_poisson",
                    "expected_goals_c": round(float(eg_c), 3) if isinstance(eg_c, (int, float)) else None,
                    "snap_top2_c": [int(x) for x in snap2_c] if isinstance(snap2_c, list) else None,
                    "total_goals_top3_c": list(c_top3),
                }
        actual_tg = (int(match.home_score) + int(match.away_score)) if actual_score is not None else None
        tg_top3_for_check = [x for x in (best_tg, second_tg, third_tg) if isinstance(x, int)]
        mv_label = label_mv if label_mv else pred.model_version

        # ===== 池化分类：基于快照 HHAD 重算信号，划分正路池/冷门池/不入池 =====
        # 正路池：HHAD 甜区（pref 1.74~2.80，sweet_low/mid/high）；冷门池：HHAD ultra_low（<1.55）
        snap_hhad = None
        if snap is not None:
            _hh = {
                "home": _as_float(getattr(snap, "hhad_home", None)),
                "draw": _as_float(getattr(snap, "hhad_draw", None)),
                "away": _as_float(getattr(snap, "hhad_away", None)),
            }
            if all(v is not None for v in _hh.values()):
                snap_hhad = _hh
        hhad_sig = _hhad_signal_v2(snap_hhad) if snap_hhad else {}
        pref_bucket = hhad_sig.get("pref_bucket")

        # ===== 大小球方向（方向+置信度门控模型）：直接读 TTG 市场定价，各档位共享一次解析 =====
        ou_all = ou_direction_all_tiers(getattr(snap, "ttg_odds_json", None)) if snap is not None else None
        if ou_all is not None:
            for _t in TIERS:
                _v = ou_all[_t]
                _st = ou_stats[_t]
                _st["signal"] += 1
                if _v["direction"] == "skip":
                    _st["skip"] += 1
                else:
                    _st["bet"] += 1
                    if actual_tg is not None:
                        _st["settled"] += 1
                        _actual_over = actual_tg > 2.5
                        if (_v["direction"] == "over") == _actual_over:
                            _st["hit"] += 1
        ou_dir = ou_all.get(ou_tier) if ou_all else None
        ou_hit = None
        if ou_dir and actual_tg is not None and ou_dir["direction"] != "skip":
            _actual_over = actual_tg > 2.5
            ou_hit = (_actual_over if ou_dir["direction"] == "over" else not _actual_over)

        # ===== SportMonks O/U 独立大小球盘口（第二信号，方向+置信度门控）=====
        # 聚合口径与验证脚本一致：2.5线 / Pinnacle 优先 / 多快照取最新 / 去抽水归一化
        ou_sm = ou_market_from_rows(ou_map.get(int(match.id)) or []) if ou_map else None
        ou_sm_dir = ou_sm["tiers"].get(ou_sm_tier) if ou_sm else None
        ou_sm_hit = None
        if ou_sm is not None:
            for _t in OU_M_TIERS:
                _sd = ou_sm["tiers"][_t]
                _st2 = ou_sm_stats[_t]
                _st2["signal"] += 1
                if _sd == "skip":
                    _st2["skip"] += 1
                else:
                    _st2["bet"] += 1
                    if actual_tg is not None:
                        _st2["settled"] += 1
                        _actual_over = actual_tg > 2.5
                        if (_sd == "over") == _actual_over:
                            _st2["hit"] += 1
            if ou_sm_dir != "skip" and actual_tg is not None:
                _actual_over = actual_tg > 2.5
                ou_sm_hit = (_actual_over if ou_sm_dir == "over" else not _actual_over)
        ou_sm_public = dict(ou_sm) if ou_sm else None
        if ou_sm_public:
            ou_sm_public["direction"] = ou_sm_dir

        item = {
            "id": int(pred.id),
            "match_id": int(match.id),
            "match_num": getattr(match, "match_num", None),
            "league_name": league_name,
            "league_id": getattr(match, "league_id", None),
            "kickoff_time": match.kickoff_time.isoformat(),
            "home_team": home_name,
            "away_team": away_name,
            "model_version": mv_label,
            "is_cfusion": bool(is_cfusion),
            "had_pref": had_pref,
            "allowed_outcomes": allowed,
            "excluded_outcomes": excluded,
            "preferred_outcome": preferred_outcome,
            "enforce_excluded": [e.get("target_outcome") for e in enforce if isinstance(e, dict) and e.get("target_outcome")],
            "total_goals_top2": [best_tg, second_tg],
            "total_goals_top3": tg_top3_for_check,
            "score_top2": [best_score_norm, second_score_norm],
            "score_top3": score_top3_norm,
            "actual_outcome": actual,
            "actual_score": actual_score,
            "actual_total_goals": actual_tg,
            "outcome_hit": (actual in allowed) if actual is not None else None,
            "preferred_hit": (actual == preferred_outcome) if (actual is not None and preferred_outcome) else None,
            "total_hit_top2": (actual_tg in (best_tg, second_tg)) if (actual_tg is not None and isinstance(best_tg, int)) else None,
            "total_hit_top3": (actual_tg in tg_top3_for_check) if (actual_tg is not None and tg_top3_for_check) else None,
            "score_hit_top2": (actual_score in (best_score_norm, second_score_norm)) if actual_score is not None else None,
            "score_hit_top3": (actual_score in score_top3_norm) if actual_score is not None else None,
            "prediction_created_at": pred.created_at.isoformat() if getattr(pred, "created_at", None) else None,
            "snapshot_time": snap.snapshot_time.isoformat() if (snap and getattr(snap, "snapshot_time", None)) else None,
            "snapshot_source": getattr(snap, "source", None),
            "pool": pool_key,
            "fav": pool_fav,
            "fav_ip": pool_fav_ip,
            "cold_dir": pool_cold_dir,
            "cold_signal": pool_cold_signal,
            "cold_type": ("strong" if pool_key == "upset" and not pool_cold_signal
                          else "warn" if pool_key == "upset" and pool_cold_signal else None),
            "pref_bucket": pref_bucket,
            "hhad_pref": hhad_sig.get("pref"),
            "hhad_pref_odd": hhad_sig.get("pref_odd"),
            "fusion": fused_meta,
            "ou_direction": ou_dir,
            "ou_hit": ou_hit,
            "ou_tier": ou_tier,
            "ou_sm": ou_sm_public,
            "ou_sm_hit": ou_sm_hit,
            "ou_sm_tier": ou_sm_tier,
        }
        items.append(item)
    return items, ou_stats, ou_sm_stats


@router.get("/predictions/live")
async def market_flow_live_predictions(
    date: str | None = None,
    model_version: str | None = None,
    source: str | None = None,
    ou_tier: str = DEFAULT_TIER,
    ou_sm_tier: str = OU_M_DEFAULT,
    db: AsyncSession = Depends(get_db),
):
    if date:
        try:
            d0 = datetime.fromisoformat(date)
        except ValueError:
            return _err("date must be ISO string", 400)
        from datetime import timedelta as _td
        start = d0.replace(hour=12, minute=0, second=0, microsecond=0)
        end = start + _td(days=1)
    else:
        start, end = _today_window_for_view()
    items, ou_stats, ou_sm_stats = await _market_flow_query(
        db, start=start, end=end, model_version=model_version, source=source,
        ou_tier=ou_tier, ou_sm_tier=ou_sm_tier,
    )
    _ou = ou_stats.get(ou_tier) or {}
    _osm = ou_sm_stats.get(ou_sm_tier) or {}
    summary = {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "default_date": start.date().isoformat(),
        "n_predicted": len(items),
        "n_with_results": sum(1 for x in items if x["actual_outcome"] is not None),
        "outcome_keep_one": sum(1 for x in items if len(x["excluded_outcomes"]) >= 1),
        "outcome_hit": sum(1 for x in items if x["outcome_hit"] is True),
        "preferred_hit": sum(1 for x in items if x["preferred_hit"] is True),
        "total_top2_hit": sum(1 for x in items if x["total_hit_top2"] is True),
        "total_top3_hit": sum(1 for x in items if x["total_hit_top3"] is True),
        "score_top2_hit": sum(1 for x in items if x["score_hit_top2"] is True),
        "score_top3_hit": sum(1 for x in items if x["score_hit_top3"] is True),
        "ou_tier": ou_tier,
        "ou_signal": _ou.get("signal", 0),
        "ou_bet": _ou.get("bet", 0),
        "ou_skip": _ou.get("skip", 0),
        "ou_hit": _ou.get("hit", 0),
        "ou_settled": _ou.get("settled", 0),
        "ou_rate": round(_ou["hit"] / _ou["settled"], 4) if _ou.get("settled") else None,
        "ou_tiers": ou_stats,
        "ou_sm_tier": ou_sm_tier,
        "ou_sm_signal": _osm.get("signal", 0),
        "ou_sm_bet": _osm.get("bet", 0),
        "ou_sm_skip": _osm.get("skip", 0),
        "ou_sm_hit": _osm.get("hit", 0),
        "ou_sm_settled": _osm.get("settled", 0),
        "ou_sm_rate": round(_osm["hit"] / _osm["settled"], 4) if _osm.get("settled") else None,
        "ou_sm_tiers": ou_sm_stats,
        "model_versions": sorted({x["model_version"] for x in items}),
    }
    return {"data": items, "summary": summary}


@router.get("/predictions/pools")
async def market_flow_pools(
    date: str | None = None,
    days: int = 180,
    trend_days: int = 30,
    db: AsyncSession = Depends(get_db),
):
    """池化分析：正路池（HHAD 甜区 pref 1.74~2.80）/ 冷门池（HHAD ultra_low <1.55）/ 不入池。

    返回当日分池明细 + 累计窗口（默认 180 天）每池命中率 + 近 N 天（trend_days，默认 30）逐日分池聚合。
    命中口径：正路池 = 实际 == HAD 偏好方向；冷门池 = 冷门率（实际 != HAD 偏好方向）。
    """
    days = max(30, min(int(days), 720))
    trend_days = max(3, min(int(trend_days), 90))
    if date:
        try:
            d0 = datetime.fromisoformat(date)
        except ValueError:
            return _err("date must be ISO string", 400)
    else:
        d0 = datetime.now()
    from datetime import timedelta as _td
    start_today = d0.replace(hour=12, minute=0, second=0, microsecond=0)
    query_start = start_today - _td(days=max(days, trend_days))
    query_end = start_today + _td(days=1)
    items, _ou_ignored, _ou_sm_ignored = await _market_flow_query(db, start=query_start, end=query_end)

    def _day_key(iso: str) -> str:
        try:
            kt = datetime.fromisoformat(iso)
        except Exception:
            return iso[:10]
        # 与当日窗口一致：12:00 UTC 为比赛日分界
        return (kt - _td(hours=12)).strftime("%Y-%m-%d")

    def _pool_of(x: dict) -> str:
        return x.get("pool") or "unpooled"

    # ---- 累计每池命中率（仅已结算场次）
    by_pool: dict[str, list] = {}
    for x in items:
        by_pool.setdefault(_pool_of(x), []).append(x)
    POOL_ORDER = ("favorite", "ambiguous", "upset", "unpooled")
    pools_meta = [
        ("favorite", "正路池", "让球方∈二选 且 HAD隐含概率≥0.58，优选=让球方", "pick"),
        ("ambiguous", "模糊池", "让球方∈二选 但隐含概率<0.58，仅双选不下单选", "none"),
        ("upset", "冷门池", "强冷门：让球方∉二选，优选=二选内非让球方隐含最高；警示冷门：平局隐含≥0.30，仅参考冷门方向", "pick"),
        ("unpooled", "不入池", "无让球方 或 负信号场次，不做选择", "none"),
    ]
    pools_out = []
    for key, label, desc, metric in pools_meta:
        rows = [x for x in by_pool.get(key, []) if x["actual_outcome"] is not None]
        n = len(rows)
        if metric == "pick":
            hit = sum(1 for x in rows if x.get("preferred_outcome") and x["preferred_outcome"] == x["actual_outcome"])
        else:
            hit = 0
        # 警示冷门（cold_signal）：preferred=None 不计入 pick 命中，冷门参考方向命中单列展示
        cold_sig_n = sum(1 for x in rows if x.get("cold_signal"))
        cold_sig_hit = sum(1 for x in rows
                          if x.get("cold_signal") and x.get("cold_dir") and x["cold_dir"] == x["actual_outcome"])
        # 双选命中率：actual ∈ 二选（模糊池/不入池无优选方向，双选口径仅作展示参考）
        both_hit = sum(1 for x in rows if x.get("allowed_outcomes") and x["actual_outcome"] in x["allowed_outcomes"])
        # rate 只统计有优选方向的场次（警示冷门 preferred=None 不计入，避免稀释）
        strong_n = n - cold_sig_n
        strong_hit = sum(1 for x in rows
                         if (not x.get("cold_signal")) and x.get("preferred_outcome")
                         and x["preferred_outcome"] == x["actual_outcome"])
        pools_out.append({
            "key": key,
            "label": label,
            "desc": desc,
            "n": n,
            "hit": hit,
            "rate": round(hit / strong_n, 4) if (strong_n and metric == "pick") else None,
            "both_hit": both_hit,
            "both_rate": round(both_hit / n, 4) if n else None,
            "cold_sig_n": cold_sig_n,
            "cold_sig_hit": cold_sig_hit,
            "cold_sig_rate": round(cold_sig_hit / cold_sig_n, 4) if cold_sig_n else None,
            "strong_n": strong_n,
            "strong_hit": strong_hit,
            "strong_rate": round(strong_hit / strong_n, 4) if strong_n else None,
            "warn_n": cold_sig_n,
            "warn_hit": cold_sig_hit,
            "warn_rate": round(cold_sig_hit / cold_sig_n, 4) if cold_sig_n else None,
            "today_n": sum(1 for x in by_pool.get(key, [])
                           if start_today <= datetime.fromisoformat(x["kickoff_time"]) < start_today + _td(days=1)),
        })

    # ---- 当日分池明细
    today: dict[str, list] = {"favorite": [], "ambiguous": [], "upset": [], "unpooled": []}
    for x in items:
        if start_today <= datetime.fromisoformat(x["kickoff_time"]) < start_today + _td(days=1):
            today.setdefault(_pool_of(x), []).append(x)

    # ---- 近 trend_days 天逐日聚合（含查询日当天，供前端做 7 日滚动 / 每日命中统计）
    first_day = (start_today - _td(days=trend_days - 1)).date()
    daily: dict[str, dict[str, list]] = {}
    for x in items:
        k = _day_key(x["kickoff_time"])
        if k < first_day.isoformat():
            continue
        daily.setdefault(k, {}).setdefault(_pool_of(x), []).append(x)
    trend = []
    for i in range(trend_days):
        day = (first_day + _td(days=i)).isoformat()
        buckets = daily.get(day, {})
        entry: dict = {"date": day}
        for pk in POOL_ORDER:
            rows = [x for x in buckets.get(pk, []) if x["actual_outcome"] is not None]
            if pk in ("favorite", "upset"):
                hit = sum(1 for x in rows if x.get("preferred_outcome") and x["preferred_outcome"] == x["actual_outcome"])
            else:
                hit = 0
            both_hit = sum(1 for x in rows if x.get("allowed_outcomes") and x["actual_outcome"] in x["allowed_outcomes"])
            # 冷门池区分统计（替代原"冷门率=actual!=fav"口径）：
            #   强冷门=方向优选命中（preferred 命中）；警示冷门=冷门参考方向命中（cold_dir 命中）
            strong_n = sum(1 for x in rows if not x.get("cold_signal"))
            strong_hit = sum(1 for x in rows
                             if (not x.get("cold_signal")) and x.get("preferred_outcome")
                             and x["preferred_outcome"] == x["actual_outcome"])
            warn_n = sum(1 for x in rows if x.get("cold_signal"))
            warn_hit = sum(1 for x in rows
                           if x.get("cold_signal") and x.get("cold_dir") and x["cold_dir"] == x["actual_outcome"])
            entry[pk] = {
                "n": len(rows),
                "hit": hit,
                "both_hit": both_hit,
                "cold_hit": strong_hit + warn_hit,
                "cold_n": strong_n + warn_n,
                "strong_n": strong_n,
                "strong_hit": strong_hit,
                "warn_n": warn_n,
                "warn_hit": warn_hit,
            }
        trend.append(entry)

    return {
        "date": d0.date().isoformat(),
        "days": days,
        "trend_days": trend_days,
        "window": {"start": query_start.isoformat(), "end": query_end.isoformat()},
        "pools": pools_out,
        "today": today,
        "trend": trend,
    }


@router.get("/predictions/history")
async def market_flow_history_report(
    start_date: str | None = None,
    end_date: str | None = None,
    model_version: str | None = None,
    source: str | None = None,
    ou_tier: str = DEFAULT_TIER,
    ou_sm_tier: str = OU_M_DEFAULT,
    db: AsyncSession = Depends(get_db),
):
    if start_date or end_date:
        try:
            s0 = datetime.fromisoformat(start_date) if start_date else None
            e0 = datetime.fromisoformat(end_date) if end_date else None
        except ValueError:
            return _err("start_date/end_date must be ISO date string", 400)
        from datetime import timedelta as _td
        start = s0.replace(hour=12, minute=0, second=0, microsecond=0) if s0 else None
        end = (e0.replace(hour=11, minute=59, second=59, microsecond=0) + _td(days=1)) if e0 else None
    else:
        from datetime import timedelta as _td
        end = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        start = end - _td(days=28)
    items, ou_stats, ou_sm_stats = await _market_flow_query(
        db, start=start, end=end, model_version=model_version, source=source,
        ou_tier=ou_tier, ou_sm_tier=ou_sm_tier,
    )

    def _matchday_date(kickoff_iso: str, match_num: str | None = None) -> str:
        # 竞彩比赛日：优先用 match_num（"周五003"这种）前缀做权威判定
        # 含义：带"周五"前缀的所有场次 = 周五比赛日，不管开球时间是周五18:00还是周六03:00
        CN_PREFIX_TO_SHIFT_DAY = {
            "周一": 0, "周二": 0, "周三": 0, "周四": 0, "周五": 0, "周六": 0, "周日": 0,
        }
        if match_num:
            import re
            _m = re.match(r"^(周[一二三四五六日])", match_num)
            if _m:
                try:
                    kt = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00").replace("+00:00", ""))
                except Exception:
                    return kickoff_iso[:10]
                if kt.tzinfo is not None:
                    kt = kt.astimezone(timezone.utc).replace(tzinfo=None)
                hr = kt.hour
                # 周X 前缀的比赛：如果 hr < 12，是周X 凌晨场，算前一天的比赛日（周X 销售期，周X-1的日期）
                # 如果 hr >= 12，是周X 下午/晚间场，直接算 kt 日期（即 kt[:10]）
                if hr < 12:
                    return (kt - _td(days=1)).strftime("%Y-%m-%d")
                else:
                    return kt.strftime("%Y-%m-%d")
        # 兜底：没带 match_num 前缀的，用 D日 12:00 ~ D+1日 11:59 = D比赛日（kt-12h）
        try:
            kt = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00").replace("+00:00", ""))
        except Exception:
            return kickoff_iso[:10]
        if kt.tzinfo is not None:
            kt = kt.astimezone(timezone.utc).replace(tzinfo=None)
        kt_shifted = kt - _td(hours=12)
        return kt_shifted.strftime("%Y-%m-%d")

    # 按 (model_version, 比赛日) 做二维汇总，按 league_name 做联赛分组
    group_by_mv: dict[str, dict] = {}
    group_by_date: dict[str, dict] = {}
    group_by_mv_date: dict[tuple[str, str], dict] = {}
    group_by_league: dict[str, dict] = {}
    for it in items:
        if not it["actual_outcome"]:
            continue
        mv = it["model_version"]
        # 分组 key：必须按 match_num 判定比赛日，不是 kt-12h
        k_date = _matchday_date(it["kickoff_time"], match_num=it.get("match_num"))
        mv = it["model_version"]
        lg = it["league_name"] or "未知联赛"
        for d, k in [
            (group_by_mv, (mv,)),
            (group_by_date, (k_date,)),
            (group_by_mv_date, (mv, k_date)),
            (group_by_league, (lg,)),
        ]:
            if k not in d:
                d[k] = {
                    "n": 0, "o_hit": 0, "p_hit": 0, "n_pick": 0, "tg2": 0, "tg3": 0, "s2": 0, "s3": 0, "keep1": 0,
                    "ou_bet": 0, "ou_skip": 0, "ou_hit_n": 0, "ou_settled": 0,
                    "ou_sm_bet": 0, "ou_sm_skip": 0, "ou_sm_hit_n": 0, "ou_sm_settled": 0,
                }
            d[k]["n"] += 1
            if it["outcome_hit"] is True:
                d[k]["o_hit"] += 1
            if it["preferred_hit"] is True:
                d[k]["p_hit"] += 1
            if it.get("preferred_outcome"):
                d[k]["n_pick"] += 1
            if it["total_hit_top2"] is True:
                d[k]["tg2"] += 1
            if it["total_hit_top3"] is True:
                d[k]["tg3"] += 1
            if it["score_hit_top2"] is True:
                d[k]["s2"] += 1
            if it["score_hit_top3"] is True:
                d[k]["s3"] += 1
            if len(it["excluded_outcomes"]) >= 1:
                d[k]["keep1"] += 1
            # 大小球 / O/U 命中（按所选档位：direction/ou_hit 已在 _market_flow_query 随档位重算）
            od = it.get("ou_direction") or {}
            if od:
                if od.get("direction") == "skip":
                    d[k]["ou_skip"] += 1
                else:
                    d[k]["ou_bet"] += 1
                    d[k]["ou_settled"] += 1
                    if it.get("ou_hit") is True:
                        d[k]["ou_hit_n"] += 1
            osm = it.get("ou_sm") or {}
            if osm:
                if osm.get("direction") == "skip":
                    d[k]["ou_sm_skip"] += 1
                else:
                    d[k]["ou_sm_bet"] += 1
                    d[k]["ou_sm_settled"] += 1
                    if it.get("ou_sm_hit") is True:
                        d[k]["ou_sm_hit_n"] += 1

    def _rate(v):
        return (v["o_hit"] / v["n"]) if v["n"] else 0.0

    def _p_rate(v):
        """ 优选命中率（by_league 用）：分母=有优选场次 """
        return (v["p_hit"] / v["n_pick"]) if v.get("n_pick") else 0.0

    def _summary(d, keys):
        out = []
        is_single = len(keys) == 1
        if is_single and len(keys) == 1 and isinstance(keys[0], str):
            dim = keys[0]
        else:
            dim = None
        first_key_is_mv = False
        if is_single and len(d) > 0:
            k0 = next(iter(d.keys()))
            if isinstance(k0[0], str):
                first_key_is_mv = k0[0].startswith("marketflow_")
        # 排序策略：
        #   keys=("date",)            → date 字典序降序（最新日期排最上：2026-08-24 → 2026-08-23 → …）
        #   keys=("league",)          → 优选命中率 (p_hit) 降序；相同则按联赛名字典序稳定排
        #   keys=("mv",)              → model_version 字典序升序
        #   keys=("mv","date") 两维  → (mv, date) 字典序升序
        if dim == "date":
            sorted_items = sorted(d.items(), key=lambda kv: kv[0], reverse=True)
        elif dim == "league":
            sorted_items = sorted(d.items(), key=lambda kv: (-_p_rate(kv[1]), kv[0][0]))
        elif is_single and not first_key_is_mv:
            # 其他未知一维：维持旧的方向命中率降序
            sorted_items = sorted(d.items(), key=lambda kv: (-_rate(kv[1]), kv[0][0]))
        else:
            sorted_items = sorted(d.items(), key=lambda kv: kv[0])
        for k, v in sorted_items:
            n = v["n"]
            n_pick = v.get("n_pick") or 0
            def pct(x, denom=None):
                _d = n if denom is None else denom
                return f"{x}/{_d}={x / _d * 100:.2f}%" if _d else "0/0=-"
            row = {}
            if len(keys) == 1:
                key = k[0]
                if isinstance(key, str) and key.startswith("marketflow_"):
                    row["model_version"] = key
                elif len(key) >= 10 and key[4] == '-' and key[7] == '-':
                    row["date"] = key
                else:
                    row["league_name"] = key
            else:
                row["model_version"], row["date"] = k
            row |= {
                "n": n,
                "n_pick": n_pick,
                "outcome_hit": pct(v["o_hit"]),
                "preferred_hit": pct(v["p_hit"], n_pick),
                "keep1_coverage": pct(v["keep1"]),
                "total_goals_top2_hit": pct(v["tg2"]),
                "total_goals_top3_hit": pct(v["tg3"]),
                "total_top3_minus_top2": f"{v['tg3'] - v['tg2']}",
                "score_top2_hit": pct(v["s2"]),
                "score_top3_hit": pct(v["s3"]),
                "score_top3_minus_top2": f"{v['s3'] - v['s2']}",
                "ou_hit": pct(v["ou_hit_n"], v["ou_settled"]),
                "ou_skip": v["ou_skip"],
                "ou_sm_hit": pct(v["ou_sm_hit_n"], v["ou_sm_settled"]),
                "ou_sm_skip": v["ou_sm_skip"],
            }
            out.append(row)
        return out

    n_pick_total = sum(1 for x in items if x["actual_outcome"] is not None and x.get("preferred_outcome"))
    _ou = ou_stats.get(ou_tier) or {}
    _osm = ou_sm_stats.get(ou_sm_tier) or {}
    overall = {
        "window_start": start.isoformat() if start else None,
        "window_end": end.isoformat() if end else None,
        "n_total": len(items),
        "n_settled": sum(1 for x in items if x["actual_outcome"] is not None),
        "n_pick_total": n_pick_total,
        "outcome_hit_total": sum(1 for x in items if x["outcome_hit"] is True),
        "preferred_hit_total": sum(1 for x in items if x["preferred_hit"] is True),
        "total_goals_top2_hit_total": sum(1 for x in items if x["total_hit_top2"] is True),
        "total_goals_top3_hit_total": sum(1 for x in items if x["total_hit_top3"] is True),
        "score_top2_hit_total": sum(1 for x in items if x["score_hit_top2"] is True),
        "score_top3_hit_total": sum(1 for x in items if x["score_hit_top3"] is True),
        "ou_tier": ou_tier,
        "ou_signal": _ou.get("signal", 0),
        "ou_bet": _ou.get("bet", 0),
        "ou_skip": _ou.get("skip", 0),
        "ou_hit": _ou.get("hit", 0),
        "ou_settled": _ou.get("settled", 0),
        "ou_rate": round(_ou["hit"] / _ou["settled"], 4) if _ou.get("settled") else None,
        "ou_tiers": ou_stats,
        "ou_sm_tier": ou_sm_tier,
        "ou_sm_signal": _osm.get("signal", 0),
        "ou_sm_bet": _osm.get("bet", 0),
        "ou_sm_skip": _osm.get("skip", 0),
        "ou_sm_hit": _osm.get("hit", 0),
        "ou_sm_settled": _osm.get("settled", 0),
        "ou_sm_rate": round(_osm["hit"] / _osm["settled"], 4) if _osm.get("settled") else None,
        "ou_sm_tiers": ou_sm_stats,
        "model_versions": sorted({x["model_version"] for x in items}),
        "by_model_version": _summary(group_by_mv, ("mv",)),
        "by_date": _summary(group_by_date, ("date",)),
        "by_league": _summary(group_by_league, ("league",)),
        "by_model_version_date": _summary(group_by_mv_date, ("mv", "date")),
    }
    return {"data": items, "summary": overall}


@router.get("/predictions/model-versions")
async def market_flow_model_versions(db: AsyncSession = Depends(get_db)):
    # 返回【全部】算法家族 MV 列表，但 history 默认合并；展示仍用 fused 口径。
    # 注：这里只列 DB 存的真实 MV，cfusion 后缀在 history runtime 阶段加。
    from sqlalchemy import func
    stmt = (
        select(
            MarketFlowPrediction.model_version.label("mv"),
            func.count(MarketFlowPrediction.id).label("cnt"),
            func.min(Match.kickoff_time).label("min_kickoff"),
            func.max(Match.kickoff_time).label("max_kickoff"),
        )
        .join(Match, Match.id == MarketFlowPrediction.match_id)
        .where(
            MarketFlowPrediction.model_version.like("marketflow_v2_%"),
            MarketFlowPrediction.model_version.notlike("%_cfusion"),
        )
        .group_by(MarketFlowPrediction.model_version)
        .order_by(func.max(Match.kickoff_time).desc())
    )
    rows = (await db.execute(stmt)).all()
    out = []
    for mv, cnt, mn, mx in rows:
        fused = f"{mv}_cfusion" if not str(mv).endswith("_cfusion") else str(mv)
        out.append({
            "model_version": fused,
            "count": int(cnt or 0),
            "min_kickoff": mn.isoformat() if mn else None,
            "max_kickoff": mx.isoformat() if mx else None,
            "note": "fused: direction/score = V2 baseline, TTG Top2 merged from ModelC Poisson",
        })
    return {"data": out}
