"""球队相关 API"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.database import get_db
from app.db.models import Team, TeamSeasonStats

router = APIRouter(prefix="/api/teams", tags=["teams"])


@router.get("/{team_id}/radar")
async def get_team_radar(team_id: int, db: AsyncSession = Depends(get_db)):
    """球队六维雷达图数据"""
    result = await db.execute(select(TeamSeasonStats).where(
        TeamSeasonStats.team_id == team_id
    ).order_by(TeamSeasonStats.season.desc()).limit(1))
    stats = result.scalar_one_or_none()
    if not stats:
        return {"data": {"attack": 50, "defense": 50, "possession": 50, "xG": 50, "form": 50, "squad": 50}}
    return {"data": {
        "attack": min(100, (stats.goals_for / max(stats.played, 1)) * 50),
        "defense": min(100, 100 - (stats.goals_against / max(stats.played, 1)) * 30),
        "possession": stats.avg_possession or 50,
        "xG": min(100, (stats.xG or 1.0) * 50),
        "form": min(100, (stats.wins / max(stats.played, 1)) * 100),
        "squad": 80,
    }}


@router.get("/{team_id}/form")
async def get_team_form(team_id: int, db: AsyncSession = Depends(get_db)):
    """球队近期状态"""
    result = await db.execute(select(TeamSeasonStats).where(
        TeamSeasonStats.team_id == team_id
    ).order_by(TeamSeasonStats.season.desc()).limit(1))
    stats = result.scalar_one_or_none()
    form_str = stats.form if stats else "------"
    return {"data": {"form": list(form_str)}}
