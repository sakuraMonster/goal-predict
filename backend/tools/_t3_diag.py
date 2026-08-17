"""t3 验证：市场盘口 vs 实际进球 vs 模型λ 的系统偏差（量化 SNAP 低进球瓶颈）
按联赛聚合: avg_actual, avg_GL, avg_λ, λ-GL偏差, SNAP命中率, 未命中场actual分布
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

OUT = os.path.join(os.path.dirname(__file__), "t3_snap_diag.txt")


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

        agg = defaultdict(lambda: {
            "n": 0, "hit": 0, "sum_actual": 0.0, "sum_gl": 0.0, "sum_lambda": 0.0,
            "miss_actual": defaultdict(int), "hit_actual": defaultdict(int),
        })
        for pred in preds:
            m = pred.match
            if not m or not m.league:
                continue
            lg = m.league.name_zh
            act = pred.actual_total_goals
            if act is None:
                continue
            try:
                fdf = await feat.extract_features(m.id)
                if fdf.empty:
                    continue
                f = fdf.iloc[0].to_dict()
                rc = model_c.predict(f, lg)
                lam = rc["expected_goals"]
                gl = float(f.get("goal_line_market", 0) or 0)
                if gl < 0.5:
                    gl = 2.5
            except Exception:
                continue

            a = agg[lg]
            a["n"] += 1
            a["sum_actual"] += act
            a["sum_gl"] += gl
            a["sum_lambda"] += lam
            act_c = min(act, 4)
            if act_c in snap_top2(lam):
                a["hit"] += 1
                a["hit_actual"][act_c] += 1
            else:
                a["miss_actual"][act_c] += 1

        p("=" * 90)
        p("近30天 ModelC 重跑: 实际 vs 市场GL vs 模型λ | SNAP top2(actual cap=4)")
        p("=" * 90)
        p(f"{'联赛':<8}{'N':>4}{'实均值':>7}{'GL均值':>7}{'λ均值':>7}{'λ-GL':>7}{'λ/实':>7}{'命中':>8}")
        total_n = total_hit = 0
        for lg in sorted(agg, key=lambda x: -agg[x]["n"]):
            a = agg[lg]
            avg_act = a["sum_actual"] / a["n"]
            avg_gl = a["sum_gl"] / a["n"]
            avg_lam = a["sum_lambda"] / a["n"]
            total_n += a["n"]
            total_hit += a["hit"]
            hit_s = f"{a['hit']}/{a['n']}={a['hit']/a['n']*100:.0f}%"
            p(f"{lg:<8}{a['n']:>4}{avg_act:>7.2f}{avg_gl:>7.2f}{avg_lam:>7.2f}"
              f"{avg_lam-avg_gl:>+7.2f}{avg_lam/avg_act if avg_act else 0:>7.2f}"
              f"{hit_s:>8}")

        p("\n== 未命中场的实际进球分布（揭示覆盖缺口）==")
        p(f"{'联赛':<8}{'N':>4}{'miss1球':>8}{'miss2球':>8}{'miss3球':>8}{'miss0球':>8}")
        for lg in sorted(agg, key=lambda x: -agg[x]["n"]):
            a = agg[lg]
            if a["n"] < 5:
                continue
            ma = a["miss_actual"]
            p(f"{lg:<8}{a['n']:>4}{ma[1]:>8}{ma[2]:>8}{ma[3]:>8}{ma[0]:>8}")

        p("\n== 1球比赛的 λ 分布（λ≥2.5 时 SNAP 覆盖不到 1）==")
        p(f"{'联赛':<8}{'1球场':>6}{'λ<2.0':>8}{'2.0-2.5':>9}{'2.5-3.0':>9}{'≥3.0':>7}{'覆盖率':>8}")
        lam_1g = defaultdict(list)
        for pred in preds:
            m = pred.match
            if not m or not m.league or pred.actual_total_goals != 1:
                continue
            try:
                fdf = await feat.extract_features(m.id)
                if fdf.empty:
                    continue
                rc = model_c.predict(fdf.iloc[0].to_dict(), m.league.name_zh)
                lam_1g[m.league.name_zh].append((lam := rc["expected_goals"], 1 if 1 in snap_top2(lam) else 0))
            except Exception:
                continue
        for lg, arr in sorted(lam_1g.items(), key=lambda x: -len(x[1])):
            if len(arr) < 3:
                continue
            n = len(arr)
            c = sum(1 for _, ok in arr if ok)
            b = [sum(1 for l, _ in arr if l < 2.0), sum(1 for l, _ in arr if 2.0 <= l < 2.5),
                 sum(1 for l, _ in arr if 2.5 <= l < 3.0), sum(1 for l, _ in arr if l >= 3.0)]
            p(f"{lg:<8}{n:>6}{b[0]:>8}{b[1]:>9}{b[2]:>9}{b[3]:>7}{f'{c}/{n}={c/n*100:.0f}%':>8}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出已写入: {OUT}")


asyncio.run(main())
