"""诊断：重预测写库链路 —— 查询窗口 / 结算判定 / 单场更新+commit / 回读确认"""
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
from app.predictor.pipeline import PredictionPipeline
from app.predictor.snap import snap_top2


async def main():
    start = datetime(2026, 7, 11, 12, 0, 0)
    end = datetime(2026, 8, 11, 12, 0, 0)

    async with async_session() as db:
        r = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        )
        preds = r.unique().scalars().all()
        settled = [p for p in preds if p.actual_total_goals is not None]
        print(f"[1] 窗口内 Prediction 共 {len(preds)} 条, 已结算 {len(settled)} 条", flush=True)

        # 单场全链路：15556 波尔图
        target = next((p for p in preds if p.match_id == 15556), None)
        if not target:
            print("[2] 15556 不在窗口内", flush=True)
            return
        print(f"[2] 15556 当前库内 λ={target.expected_goals_c} top2={target.snap_top2_c}", flush=True)

        feat = FeatureEngineerB(db)
        model_c = ModelC()
        fdf = await feat.extract_features(15556)
        print(f"[3] features 行数={len(fdf)}", flush=True)
        if fdf.empty:
            print("[3] features 为空, 无法重算", flush=True)
            return
        rc = model_c.predict(fdf.iloc[0].to_dict(), "葡超")
        lam = rc["expected_goals"]
        print(f"[4] model_c 重算 λ={lam:.2f}", flush=True)
        rc = PredictionPipeline._apply_lambda_calibration(rc, 0.92)
        new_lam = rc["expected_goals"]
        new_top2 = snap_top2(new_lam)
        print(f"[5] ×0.92 后 λ={new_lam:.2f} top2={new_top2}", flush=True)

        target.expected_goals_c = new_lam
        target.snap_top2_c = new_top2
        await db.commit()
        print("[6] commit 完成", flush=True)

    # 新会话回读确认
    async with async_session() as db2:
        r2 = await db2.execute(select(Prediction).where(Prediction.match_id == 15556))
        p2 = r2.scalar_one_or_none()
        print(f"[7] 回读 15556: λ={p2.expected_goals_c} top2={p2.snap_top2_c}", flush=True)
        print("结论:", "写库成功" if p2 and p2.expected_goals_c == new_lam else "写库失败", flush=True)


asyncio.run(main())
