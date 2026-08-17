"""t4 深挖：Model B 葡超 8 场 —— 现有 0.92 后验对 Model B 是利是弊
复现 pipeline B 链: model_b.predict → goal_market_adjust → market_attenuation → (±0.92 calib)
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
from app.predictor.features_b import FeatureEngineerB
from app.predictor.pipeline import PredictionPipeline
from app.predictor.snap import snap_top2


async def main():
    start = datetime(2026, 7, 11, 12, 0, 0)
    end = datetime(2026, 8, 11, 12, 0, 0)

    async with async_session() as db:
        feat = FeatureEngineerB(db)
        pipe = PredictionPipeline(db)
        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        )
        preds = result.unique().scalars().all()

        hits = {"online": 0, "no_calib": 0, "with_092": 0}
        n = 0
        print(f"{'比赛':<7}{'主队':<12}{'客队':<12}{'实':>3}{'线上B':>8}{'√':>3}{'λ无':>7}{'top2无':>8}{'√':>3}{'λ×.92':>8}{'top2×':>8}{'√':>3}")
        for pred in preds:
            m = pred.match
            if not m or not m.league or m.league.name_zh != "葡超":
                continue
            act = pred.actual_total_goals
            if act is None:
                continue
            act_c = min(act, 4)
            snap_online = pred.snap_top2 or []
            try:
                fdf = await feat.extract_features(m.id)
                if fdf.empty:
                    print(f"{m.id:<7} FEATURES EMPTY")
                    continue
                rb = pipe.model_b.predict(fdf)
                features = fdf.iloc[0].to_dict()
                rb = pipe._apply_goal_market_adjustment(rb, features, "葡超")
                rb = pipe._apply_market_attenuation(rb, features, "葡超")
                lam_no = rb["expected_goals"]
                top2_no = snap_top2(lam_no)
                rb2 = pipe._apply_lambda_calibration(rb, 0.92)
                lam_cal = rb2["expected_goals"]
                top2_cal = snap_top2(lam_cal)
            except Exception as e:
                print(f"{m.id:<7} ERR: {type(e).__name__}: {e}")
                continue
            n += 1
            hits["online"] += act_c in snap_online
            hits["no_calib"] += act_c in top2_no
            hits["with_092"] += act_c in top2_cal
            print(f"{m.id:<7}{m.home_team_name or '?':<12}{m.away_team_name or '?':<12}{act_c:>3}"
                  f"{str(snap_online):>8}{'√' if act_c in snap_online else '×':>3}"
                  f"{lam_no:>7.2f}{str(top2_no):>8}{'√' if act_c in top2_no else '×':>3}"
                  f"{lam_cal:>8.2f}{str(top2_cal):>8}{'√' if act_c in top2_cal else '×':>3}")
        print(f"\n结算: 线上B {hits['online']}/{n}  无calib {hits['no_calib']}/{n}  ×0.92 {hits['with_092']}/{n}")


asyncio.run(main())
