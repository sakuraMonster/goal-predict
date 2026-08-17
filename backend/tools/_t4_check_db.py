"""只读验证：存量 Prediction.snap_top2_c 是否仍是旧值（未乘 Model C 葡超后验）"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction
from app.predictor.snap import snap_top2


async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match))
            .where(Prediction.match_id.in_([15556, 15557, 15546]))
        )
        for p in r.unique().scalars().all():
            m = p.match
            new_lam = (p.expected_goals_c or 0) * 0.92
            print(f"match={p.match_id} {m.home_team_name if m else '?'} vs {m.away_team_name if m else '?'}"
                  f" | 库内 expected_goals_c={p.expected_goals_c} snap_top2_c={p.snap_top2_c}"
                  f" | 新逻辑 λ={new_lam:.2f} top2={snap_top2(new_lam)}")


asyncio.run(main())
