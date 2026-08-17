"""t3 葡超校准模拟：重跑葡超近30天，输出每场明细 + 不同后验系数下的 SNAP 命中
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

        rows = []
        for pred in preds:
            m = pred.match
            if not m or not m.league or m.league.name_zh != "葡超":
                continue
            act = pred.actual_total_goals
            if act is None:
                continue
            try:
                fdf = await feat.extract_features(m.id)
                if fdf.empty:
                    continue
                rc = model_c.predict(fdf.iloc[0].to_dict(), "葡超")
                lam = rc["expected_goals"]
                gl = float(fdf.iloc[0].get("goal_line_market", 0) or 0)
            except Exception as e:
                print(f"  skip {m.id}: {e}")
                continue
            rows.append((m.kickoff_time, m.id, m.home_team_name, m.away_team_name, act, lam, gl))

        rows.sort(key=lambda x: x[0])
        print(f"葡超近30天已完成比赛: {len(rows)} 场\n")
        print(f"{'比赛':<12}{'主队':<12}{'客队':<12}{'实际':>4}{'λ':>6}{'GL':>6}{'top2':>10}{'命中':>5} | ×0.89×0.92")
        for kt, mid, h, a, act, lam, gl in rows:
            top = snap_top2(lam)
            hit = act in top
            top89 = snap_top2(lam * 0.89)
            top92 = snap_top2(lam * 0.92)
            h89 = "√" if act in top89 else "×"
            h92 = "√" if act in top92 else "×"
            print(f"{str(mid):<12}{h or '?':<12}{a or '?':<12}{act:>4}{lam:>6.2f}{gl:>6.2f}{str(top):>10}{'√' if hit else '×':>5} | {h89}      {h92}")

        hits = [(r[4] in snap_top2(r[5])) for r in rows]
        hits89 = [(r[4] in snap_top2(r[5] * 0.89)) for r in rows]
        hits92 = [(r[4] in snap_top2(r[5] * 0.92)) for r in rows]
        print(f"\n命中: 原 {sum(hits)}/{len(rows)} | ×0.89 {sum(hits89)}/{len(rows)} | ×0.92 {sum(hits92)}/{len(rows)}")


asyncio.run(main())
