"""t4 深挖：逐场对比线上 vs 重跑（含calib）命中差异，聚焦下降联赛（挪超/巴甲/美职联）
"""
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
from app.predictor.pipeline import PredictionPipeline


async def main():
    start = datetime(2026, 7, 11, 12, 0, 0)
    end = datetime(2026, 8, 11, 12, 0, 0)
    calib_map = PredictionPipeline.LEAGUE_LAMBDA_CALIBRATION

    async with async_session() as db:
        feat = FeatureEngineerB(db)
        model_c = ModelC()
        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        )
        preds = result.unique().scalars().all()

        rows = []
        for pred in preds:
            m = pred.match
            if not m or not m.league:
                continue
            lg = m.league.name_zh
            if lg not in ("挪超", "巴甲", "美职联"):
                continue
            act = pred.actual_total_goals
            if act is None:
                continue
            snap_online = pred.snap_top2_c or pred.snap_top2 or []
            try:
                fdf = await feat.extract_features(m.id)
                if fdf.empty:
                    continue
                lam = model_c.predict(fdf.iloc[0].to_dict(), lg)["expected_goals"]
                lam *= calib_map.get(lg, 1.0)
            except Exception as e:
                print(f"  skip {m.id}: {e}")
                continue
            rows.append((m.kickoff_time, m.id, lg, m.home_team_name, m.away_team_name, act,
                         snap_online, act in snap_online, lam, snap_top2(lam), act in snap_top2(lam)))

        rows.sort(key=lambda x: (x[2], x[0]))
        print(f"{'联赛':<5}{'比赛':<7}{'主队':<14}{'客队':<14}{'实':>3}{'线上top2':>11}{'√':>3}{'重跑λ':>8}{'重跑top2':>10}{'√':>3}")
        for kt, mid, lg, h, a, act, so, soh, lam, rt, rth in rows:
            print(f"{lg:<5}{mid:<7}{h or '?':<14}{a or '?':<14}{act:>3}{str(so):>11}{'√' if soh else '×':>3}"
                  f"{lam:>8.2f}{str(rt):>10}{'√' if rth else '×':>3}")


asyncio.run(main())
