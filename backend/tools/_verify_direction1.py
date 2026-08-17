"""量化验证 方向1：盘口信任度门控
按 market_confidence（盘口可信度）分层，对比「纯盘口 goal_line」 vs 「完整 Model C」命中率，
验证：盘口可信时 Model C 是否在减值、盘口不可信时 Model C 是否在增值。
"""
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

from app.db.database import async_session
from app.db.models import Prediction, Match
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

BEIJING_TZ = timezone(timedelta(hours=8))


def hit(tg, snap):
    return tg in list(snap) if snap else False


async def main():
    now = datetime.now(BEIJING_TZ).replace(tzinfo=None)
    since = now - timedelta(days=30)
    model_c = ModelC()

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= since, Prediction.kickoff_time < now))
            .order_by(Prediction.kickoff_time)
        )
        preds = list(result.unique().scalars().all())
        settled = [p for p in preds if p.actual_home_score is not None]

        # 收集每场的三层指标
        records = []
        for p in settled:
            tg = (p.actual_home_score or 0) + (p.actual_away_score or 0)
            ln = p.match.league.name_zh if (p.match and p.match.league) else "未知"
            try:
                features_df = await feat_engine.extract_features(p.match_id)
                if features_df.empty:
                    continue
                features = features_df.iloc[0].to_dict()
                res = model_c.predict(features, ln)
                d = res["detail"]
                gl = d["goal_line"]
                if gl < 0.5:
                    continue
                records.append({
                    "tg": tg,
                    "gl": gl,
                    "lc": res["expected_goals"],
                    "conf": d["market_confidence"],
                    "divergence": d["divergence"],
                    "mw": d["market_weight"],
                    "lm": d["lambda_market"],
                    "lf": d["lambda_fundamental"],
                })
            except Exception:
                pass

        n = len(records)
        print(f"近30天已结算可分析: {n} 场\n")

        # 1. 整体三配置对比
        gl_hit = sum(hit(r["tg"], snap_top2(r["gl"])) for r in records)
        lc_hit = sum(hit(r["tg"], snap_top2(r["lc"])) for r in records)
        print("=" * 90)
        print("【整体】三配置命中率")
        print(f"  纯盘口 goal_line : {gl_hit/n*100:.1f}%")
        print(f"  完整 Model C λc  : {lc_hit/n*100:.1f}%")
        print(f"  差值(ModelC-盘口): {(lc_hit-gl_hit)/n*100:+.1f}pp")

        # 2. market_confidence 分层
        print("\n" + "=" * 90)
        print("【market_confidence 分层】纯盘口 vs 完整 Model C")
        confs = sorted(r["conf"] for r in records)
        q1 = confs[int(n * 0.33)]
        q2 = confs[int(n * 0.66)]
        print(f"  三分位点: low<{q1:.2f}  mid {q1:.2f}~{q2:.2f}  high>{q2:.2f}")

        layers = [("低可信", lambda c: c < q1), ("中可信", lambda c: q1 <= c <= q2), ("高可信", lambda c: c > q2)]
        for label, cond in layers:
            sub = [r for r in records if cond(r["conf"])]
            if not sub:
                continue
            m = len(sub)
            glh = sum(hit(r["tg"], snap_top2(r["gl"])) for r in sub)
            lch = sum(hit(r["tg"], snap_top2(r["lc"])) for r in sub)
            print(f"  {label}(n={m:>3}): 盘口 {glh/m*100:5.1f}%  ModelC {lch/m*100:5.1f}%  差值 {(lch-glh)/m*100:+5.1f}pp")

        # 3. divergence 分层（盘口-基本面背离）
        print("\n" + "=" * 90)
        print("【divergence 分层】纯盘口 vs 完整 Model C（divergence=盘口-基本面）")
        divs = [r["divergence"] for r in records]
        # 按绝对值分（背离大=盘口可能被诱导）
        abs_divs = sorted(abs(d) for d in divs)
        a1 = abs_divs[int(n * 0.33)]
        a2 = abs_divs[int(n * 0.66)]
        print(f"  |divergence| 三分位: <{a1:.2f} / {a1:.2f}~{a2:.2f} / >{a2:.2f}")

        div_layers = [
            ("背离小", lambda r: abs(r["divergence"]) < a1),
            ("背离中", lambda r: a1 <= abs(r["divergence"]) <= a2),
            ("背离大", lambda r: abs(r["divergence"]) > a2),
        ]
        for label, cond in div_layers:
            sub = [r for r in records if cond(r)]
            if not sub:
                continue
            m = len(sub)
            glh = sum(hit(r["tg"], snap_top2(r["gl"])) for r in sub)
            lch = sum(hit(r["tg"], snap_top2(r["lc"])) for r in sub)
            print(f"  {label}(n={m:>3}): 盘口 {glh/m*100:5.1f}%  ModelC {lch/m*100:5.1f}%  差值 {(lch-glh)/m*100:+5.1f}pp")


if __name__ == "__main__":
    asyncio.run(main())
