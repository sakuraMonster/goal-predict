"""全联赛近30天重跑回归：线上口径 vs 修复后重跑口径命中率对比"""
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

OUT = os.path.join(os.path.dirname(__file__), "rerun_all_leagues.txt")


async def main():
    start = datetime(2026, 7, 11, 12, 0, 0)
    end = datetime(2026, 8, 11, 12, 0, 0)

    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        feat = FeatureEngineerB(db)
        model_c = ModelC()

        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        )
        preds = result.unique().scalars().all()

        # 线上口径
        online = defaultdict(lambda: [0, 0])  # lg -> [hit, settled]
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
            # 线上
            snap_online = pred.snap_top2_c or pred.snap_top2
            if snap_online and act_c in snap_online:
                online[lg][0] += 1
            online[lg][1] += 1
            # 重跑
            try:
                fdf = await feat.extract_features(m.id)
                if fdf.empty:
                    continue
                rc = model_c.predict(fdf.iloc[0].to_dict(), lg)
                if act_c in snap_top2(rc["expected_goals"]):
                    rerun[lg][0] += 1
                rerun[lg][1] += 1
            except Exception:
                continue

        p("=" * 76)
        p("近30天 ModelC 命中率对比：线上(存库) vs 重跑(修复后) | 口径 snap_top2, actual cap=4")
        p("=" * 76)
        p(f"{'联赛':<10}{'结算':>6}{'线上':>10}{'重跑':>10}{'变化':>8}")
        all_lg = sorted(set(list(online) + list(rerun)), key=lambda x: -rerun[x][1])
        for lg in all_lg:
            o = online.get(lg, [0, 0])
            r = rerun.get(lg, [0, 0])
            oa = o[0] / o[1] * 100 if o[1] else 0
            ra = r[0] / r[1] * 100 if r[1] else 0
            p(f"{lg:<10}{r[1]:>6}{f'{o[0]}/{o[1]}={oa:.0f}%':>10}{f'{r[0]}/{r[1]}={ra:.0f}%':>10}{f'{ra-oa:+.0f}pp':>8}")
        oh = sum(v[0] for v in online.values()); ot = sum(v[1] for v in online.values())
        rh = sum(v[0] for v in rerun.values()); rt = sum(v[1] for v in rerun.values())
        p("-" * 76)
        p(f"{'总计':<10}{rt:>6}{f'{oh}/{ot}={oh/ot*100:.1f}%':>10}{f'{rh}/{rt}={rh/rt*100:.1f}%':>10}"
          f"{f'{rh/rt*100-oh/ot*100:+.1f}pp':>8}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出已写入: {OUT}")


asyncio.run(main())
