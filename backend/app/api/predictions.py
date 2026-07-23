"""预测相关 API"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from datetime import datetime, timedelta
from app.db.database import get_db
from app.db.models import Prediction, Match, League
from app.predictor.pipeline import PredictionPipeline

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


@router.get("/{match_id}")
async def get_prediction(match_id: int, db: AsyncSession = Depends(get_db)):
    """单场预测完整数据"""
    result = await db.execute(select(Prediction).where(Prediction.match_id == match_id))
    pred = result.scalar_one_or_none()
    if not pred:
        return {"data": None, "message": "预测数据尚未生成"}
    return {"data": {
        "home_prob": pred.home_prob, "draw_prob": pred.draw_prob, "away_prob": pred.away_prob,
        "handicap_home_prob": pred.handicap_home_prob, "handicap_draw_prob": pred.handicap_draw_prob, "handicap_away_prob": pred.handicap_away_prob,
        "expected_goals": pred.expected_goals, "over_2_5_prob": pred.over_2_5_prob,
        "goal_distribution": pred.goal_distribution,
        "score_top5_json": pred.score_top5_json,
        "confidence_level": pred.confidence_level, "is_cold_match": pred.is_cold_match,
        "summary_text": pred.summary_text, "key_factors": pred.key_factors, "risk_warning": pred.risk_warning,
        "model_version": pred.model_version, "created_at": str(pred.created_at),
    }}


@router.get("/review/summary")
async def get_review_summary(days: int = Query(30), db: AsyncSession = Depends(get_db)):
    """复盘统计概览"""
    return {
        "data": {
            "total_predictions": 0, "wl_accuracy": 0.0, "wl_accuracy_last_week": 0.0,
            "handicap_accuracy": 0.0, "goal_accuracy": 0.0, "score_top3_accuracy": 0.0,
        }
    }


@router.get("/review/trend")
async def get_accuracy_trend(days: int = Query(30), db: AsyncSession = Depends(get_db)):
    """每日准确率趋势"""
    return {"data": []}


@router.get("/pnl")
async def get_pnl(days: int = Query(30), db: AsyncSession = Depends(get_db)):
    """盈亏模拟"""
    return {"data": {"total_pnl": 0.0, "roi": 0.0, "series": []}}


@router.get("/models/versions")
async def get_model_versions(db: AsyncSession = Depends(get_db)):
    """模型版本对比"""
    result = await db.execute(
        select(Prediction.model_version, func.count(Prediction.id).label("total"))
        .group_by(Prediction.model_version).order_by(Prediction.model_version.desc())
    )
    versions = result.all()
    return {"data": [{"version": v[0], "total_predictions": v[1]} for v in versions]}
