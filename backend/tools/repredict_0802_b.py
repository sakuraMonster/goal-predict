"""对 08-02 竞彩周期比赛重跑 Model B 预测，保留 Model A 结果不变"""
import asyncio, sys, math
from datetime import datetime
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.pipeline import PredictionPipeline

SNAP_DOWN = 0.10
SNAP_UP = 0.90


def judge_goals_snap(total_goals: int, expected_goals: float) -> int:
    frac = expected_goals - int(expected_goals)
    effective = expected_goals
    if frac < SNAP_DOWN:
        effective = int(expected_goals)
    elif frac > SNAP_UP:
        effective = int(expected_goals) + 1
    dists = [(i, abs(effective - i)) for i in range(7)]
    dists.sort(key=lambda x