"""t4 深挖：欧罗巴/荷乙 下降联赛逐场定位（小样本）"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2


async def main():
    start = datetime(2026, 7, 11, 12, 0, 0)
    end = datetime(2026, 8, 11, 12, 0, 0)

    async with async_session() as db:
        feat = FeatureEngineerB(db)
        model_c = ModelC()
        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        )
        preds = result.unique().scalars().all()

        for lg_want in ("欧罗巴", "荷乙"):
            print(f"== {lg_want} ==")
            print(f"{'比赛':<7}{'主队':<14}{'客队':<14}{'实':>3}{'线上top2':>11}{'√':>3}{'λ':>7}{'重跑top2':>10}{'√':>3}")
            for pred in preds:
                m = pred.match
                if not m or not m.league or m.league.name_zh != lg_want:
                    continue
                act = pred.actual_total_goals
                if act is None:
                    continue
                act_c = min(act, 4)
                snap_online = pred.snap_top2_c or pred.snap_top2 or []
                try:
                    fdf = await feat.extract_features(m.id)
                    if fdf.empty:
                        print(f"{m.id:<7} FEATURES EMPTY")
                        continue
                    lam = model_c.predict(fdf.iloc[0].to_dict(), lg_want)["expected_goals"]
                except Exception as e:
                    print(f"{m.id:<7} ERR: {e}")
                    continue
                rt = snap_top2(lam)
                print(f"{m.id:<7}{m.home_team_name or '?':<14}{m.away_team_name or '?':<14}{act_c:>3}"
                      f"{str(snap_online):>11}{'√' if act_c in snap_online else '×':>3}"
                      f"{lam:>7.2f}{str(rt):>10}{'√' if act_c in rt else '×':>3}")
            print()


asyncio.run(main())
