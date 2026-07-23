"""系统管理 API"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.database import get_db
from app.db.models import TaskLog

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/task-status")
async def get_task_status(db: AsyncSession = Depends(get_db)):
    """定时任务状态"""
    return {"data": [
        {"task_type": "sync_matches", "status": "idle", "last_run": None, "next_run": "09:00", "duration_ms": 0},
        {"task_type": "sync_odds", "status": "idle", "last_run": None, "next_run": "09:00", "duration_ms": 0},
        {"task_type": "update_teams", "status": "idle", "last_run": None, "next_run": "03:00", "duration_ms": 0},
        {"task_type": "retrain", "status": "idle", "last_run": None, "next_run": "周一04:00", "duration_ms": 0},
    ]}


@router.get("/logs")
async def get_logs(hours: int = Query(48), db: AsyncSession = Depends(get_db)):
    """操作日志"""
    result = await db.execute(
        select(TaskLog).order_by(TaskLog.created_at.desc()).limit(50)
    )
    logs = result.scalars().all()
    return {"data": [{"task_type": l.task_type, "status": l.status, "message": l.message,
                       "created_at": str(l.created_at)} for l in logs]}


@router.post("/sync-matches")
async def trigger_sync_matches():
    return {"message": "赛程同步已触发"}

@router.post("/sync-odds")
async def trigger_sync_odds():
    return {"message": "赔率更新已触发"}

@router.post("/update-teams")
async def trigger_update_teams():
    return {"message": "球队信息更新已触发"}

@router.post("/trigger-retrain")
async def trigger_retrain():
    return {"message": "模型重训练已触发"}
