"""重预测：近30天已结算 Prediction 的 Model C 字段重算并写库
口径与 pipeline.predict 一致: model_c.predict → 葡超 × MODELC_LAMBDA_CALIBRATION(0.92)
仅更新 expected_goals_c / snap_top2_c，不动其他字段（expected_goals 属 Model B）
"""
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

LOG = os.path.join(os.path.dirname(__file__), "repredict_modelc_30d.log")


def log(msg: str):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg, flush=True)


async def main():
    if os.path.exists(LOG):
        os.remove(LOG)
    start = datetime(2026, 7, 11, 12, 0, 0)
    end = datetime(2026, 8, 11, 12, 0, 0)
    mc_calib_map = PredictionPipeline.MODELC_LAMBDA_CALIBRATION

    async with async_session() as db:
        feat = FeatureEngineerB(db)
        model_c = ModelC()
        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        )
        preds = result.unique().scalars().all()

        updated, skipped, failed = 0, 0, 0
        changed = []
        for pred in preds:
            m = pred.match
            if not m or not m.league:
                skipped += 1
                continue
            lg = m.league.name_zh
            if pred.actual_total_goals is None:
                skipped += 1  # 未结算，定时预测任务会按新逻辑处理
                continue
            try:
                fdf = await feat.extract_features(m.id)
                if fdf.empty:
                    failed += 1
                    log(f"  FAIL match={m.id}: features EMPTY")
                    continue
                rc = model_c.predict(fdf.iloc[0].to_dict(), lg)
                if lg in mc_calib_map:
                    rc = PredictionPipeline._apply_lambda_calibration(rc, mc_calib_map[lg])
            except Exception:
                failed += 1
                log(f"  FAIL match={m.id} ({lg}): {traceback.format_exc()}")
                continue

            new_lam = rc["expected_goals"]
            new_top2 = snap_top2(new_lam)
            if pred.expected_goals_c != new_lam or pred.snap_top2_c != new_top2:
                changed.append((m.id, lg, pred.expected_goals_c, pred.snap_top2_c, round(new_lam, 2), new_top2))
            pred.expected_goals_c = new_lam
            pred.snap_top2_c = new_top2
            updated += 1

        await db.commit()
        log(f"更新 {updated} 条, 跳过(未结算/无联赛) {skipped}, 失败 {failed}")
        log(f"值有变化的 {len(changed)} 条:")
        log(f"{'比赛':<7}{'联赛':<6}{'旧λ':>8}{'旧top2':>9}{'新λ':>8}{'新top2':>9}")
        for mid, lg, olam, otop, nlam, ntop in changed:
            log(f"{mid:<7}{lg:<6}{olam or 0:>8.2f}{str(otop):>9}{nlam:>8.2f}{str(ntop):>9}")


asyncio.run(main())
