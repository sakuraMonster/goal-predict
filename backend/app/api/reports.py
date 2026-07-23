"""报告相关 API"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.db.database import get_db
from app.db.models import Prediction, Match, League, Team

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/daily/summary")
async def get_daily_summary(db: AsyncSession = Depends(get_db)):
    """每日报告概览统计"""
    return {
        "data": {
            "total_matches": 0, "cold_match_count": 0,
            "avg_confidence": 0.0, "model_version": "v0.1.0",
            "league_count": 0
        }
    }


@router.get("/daily")
async def get_daily_report(db: AsyncSession = Depends(get_db)):
    """完整预测报告数据"""
    return {"data": []}


@router.get("/export/csv")
async def export_csv(db: AsyncSession = Depends(get_db)):
    """导出 CSV"""
    return {"message": "CSV export endpoint"}
