"""t4 最终定稿验证: 线上 vs 修复后重跑(数据修复 + Model C 专属后验 葡超×0.92) | snap_top2_c cap4
口径与生产 pipeline.predict 一致: model_c.predict → 仅葡超乘 MODELC_LAMBDA_CALIBRATION
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collections import defaultdict
from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2
from app.predictor.pipeline import PredictionPipeline

OUT = os.path.join(os.path.dirname(__file__), "t4_final_result.txt")


async def main():
    start = datetime(2026, 7, 11, 12, 0, 0)
    end = datetime(2026, 8, 11, 12, 0, 0)

    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        feat = FeatureEngineerB(db)
        model_c = ModelC()
        mc_calib_map = PredictionPipeline.MODELC_LAMBDA_CALIBRATION

        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        )
        preds = result.unique().scalars().all()

        online = defaultdict(lambda: [0, 0])
        rerun = defaultdict(lambda: [0, 0])
        for pred in preds:
            m = pred.match
            if not m or not m.league:
                continue
            lg = m.league.name_zh
            act = pred.actual_total_goals
            if act is None:
                continue
            act_c = min(act, 4)
            snap_online = pred.snap_top2_c or pred.snap_top2
            if snap_online and act_c in snap_online:
                online[lg][0] += 1
            online[lg][1] += 1
            try:
                fdf = await feat.extract_features(m.id)
                if fdf.empty:
                    continue
                lam = model_c.predict(fdf.iloc[0].to_dict(), lg)["expected_goals"]
                # Model C 专属后验（与 pipeline.predict 一致）
                if lg in mc_calib_map:
                    lam *= mc_calib_map[lg]
                if act_c in snap_top2(lam):
                    rerun[lg][0] += 1
                rerun[lg][1] += 1
            except Exception:
                continue

        p("=" * 78)
        p("最终定稿: 线上(存库) vs 修复后重跑(数据修复 + Model C 葡超×0.92) | snap_top2_c cap4")
        p("=" * 78)
        p(f"{'联赛':<10}{'结算':>6}{'线上':>12}{'重跑':>12}{'变化':>8}")
        all_lg = sorted(set(list(online) + list(rerun)), key=lambda x: -rerun[x][1])
        for lg in all_lg:
            o = online.get(lg, [0, 0]); r = rerun.get(lg, [0, 0])
            oa = o[0] / o[1] * 100 if o[1] else 0
            ra = r[0] / r[1] * 100 if r[1] else 0
            p(f"{lg:<10}{r[1]:>6}{f'{o[0]}/{o[1]}={oa:.0f}%':>12}{f'{r[0]}/{r[1]}={ra:.0f}%':>12}{f'{ra-oa:+.0f}pp':>8}")
        oh = sum(v[0] for v in online.values()); ot = sum(v[1] for v in online.values())
        rh = sum(v[0] for v in rerun.values()); rt = sum(v[1] for v in rerun.values())
        p("-" * 78)
        p(f"{'总计':<10}{rt:>6}{f'{oh}/{ot}={oh/ot*100:.1f}%':>12}{f'{rh}/{rt}={rh/rt*100:.1f}%':>12}"
          f"{f'{rh/rt*100-oh/ot*100:+.1f}pp':>8}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出已写入: {OUT}")


asyncio.run(main())
