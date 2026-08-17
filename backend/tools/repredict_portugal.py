"""重预测(仅葡超已结算)：验证 Model C 后验链路修复后葡超 8 场可正确写库"""
import asyncio
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.pipeline import PredictionPipeline
from app.predictor.snap import snap_top2

LOG = os.path.join(os.path.dirname(__file__), "repredict_portugal.log")


def log(msg: str):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg, flush=True)


async def main():
    if os.path.exists(LOG):
        os.remove(LOG)
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
        updated, failed = 0, 0
        for pred in preds:
            m = pred.match
            if not m or not m.league or m.league.name_zh != "葡超":
                continue
            if pred.actual_total_goals is None:
                log(f"skip {m.id}: 未结算")
                continue
            try:
                fdf = await feat.extract_features(m.id)
                if fdf.empty:
                    failed += 1
                    log(f"FAIL {m.id}: features EMPTY")
                    continue
                rc = model_c.predict(fdf.iloc[0].to_dict(), "葡超")
                rc = PredictionPipeline._apply_lambda_calibration(rc, 0.92)
            except Exception:
                failed += 1
                log(f"FAIL {m.id}: {traceback.format_exc()}")
                continue
            new_lam = rc["expected_goals"]
            new_top2 = snap_top2(new_lam)
            log(f"UPD {m.id}: {pred.expected_goals_c} {pred.snap_top2_c} -> {new_lam:.2f} {new_top2}")
            pred.expected_goals_c = new_lam
            pred.snap_top2_c = new_top2
            updated += 1
        await db.commit()
        log(f"葡超更新 {updated} 条, 失败 {failed}")


asyncio.run(main())
