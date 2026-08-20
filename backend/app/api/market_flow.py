from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import JczqPlayOddsSnapshot, MarketFlowPrediction, Match, Team
from app.predictor.models.market_flow import MarketFlowEngine

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

    crs = payload.get("crs")
    if not isinstance(crs, dict):
        return _err("crs must be object", 400)

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

    model_version = payload.get("model_version") or "marketflow_v1"
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

    pred_result = await db.execute(select(MarketFlowPrediction).where(MarketFlowPrediction.match_id == match_id))
    pred = pred_result.scalar_one_or_none()

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

    source = payload.get("source")
    if source is not None and (not isinstance(source, str) or not source):
        return _err("source must be string", 400)

    overwrite = payload.get("overwrite", False)
    if not isinstance(overwrite, bool):
        return _err("overwrite must be bool", 400)

    model_version = "marketflow_v1"

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

    engine = MarketFlowEngine()

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

            now = datetime.utcnow()
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
