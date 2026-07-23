"""名称映射管理 API"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.db.database import get_db
from app.db.models import Team, TeamAlias, League, LeagueAlias

router = APIRouter(prefix="/api/mappings", tags=["mappings"])


@router.get("/stats")
async def get_mapping_stats(type: str = "team", db: AsyncSession = Depends(get_db)):
    """映射统计"""
    return {"data": {"total": 0, "auto_matched": 0, "pending": 0}}


@router.get("/pending")
async def get_pending_mappings(type: str = "team", db: AsyncSession = Depends(get_db)):
    """待确认映射队列"""
    return {"data": []}


@router.get("/teams")
async def get_team_mappings(status: str = "all", db: AsyncSession = Depends(get_db)):
    """已确认映射列表"""
    return {"data": []}


@router.post("/confirm")
async def confirm_mapping(payload: dict, db: AsyncSession = Depends(get_db)):
    """确认映射"""
    return {"status": "ok"}


@router.post("/add-alias")
async def add_alias(payload: dict, db: AsyncSession = Depends(get_db)):
    """添加别名"""
    return {"status": "ok"}
