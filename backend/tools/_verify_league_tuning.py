"""量化验证「分联赛针对性微调」的收益上界（近30天已结算）
对每个联赛，在三种配置中选命中率最高者，汇总得到分联赛最优上界：
  配置1: 纯盘口 goal_line
  配置2: market侧 goal_line × calib × (1+adj)
  配置3: 完整融合 λc（含基本面）
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

        agg = defaultdict(lambda: {"n": 0, "gl": 0, "mkt": 0, "lc": 0})

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
                lm = d["lambda_market"]
                lc = res["expected_goals"]
                if gl < 0.5:
                    continue
                a = agg[ln]
                a["n"] += 1
                a["gl"] += hit(tg, snap_top2(gl))
                a["mkt"] += hit(tg, snap_top2(lm))
                a["lc"] += hit(tg, snap_top2(lc))
            except Exception:
                pass

        print("=" * 96)
        print(f"{'联赛':<8}{'n':>4}  {'盘口':>7}  {'market侧':>8}  {'λc融合':>8}  {'最优配置':>9}  {'相对λc':>8}")
        print("=" * 96)

        total = {"n": 0, "gl": 0, "mkt": 0, "lc": 0, "best": 0}
        rows = []
        for ln, a in agg.items():
            if a["n"] < 3:
                continue
            gl = a["gl"] / a["n"] * 100
            mkt = a["mkt"] / a["n"] * 100
            lc = a["lc"] / a["n"] * 100
            best = max(gl, mkt, lc)
            best_name = ["盘口", "market侧", "λc融合"][[gl, mkt, lc].index(best)]
            rows.append((ln, a["n"], gl, mkt, lc, best_name, best - lc))
            for k, v in [("n", a["n"]), ("gl", a["gl"]), ("mkt", a["mkt"]), ("lc", a["lc"])]:
                total[k] += v
            total["best"] += int(best * a["n"] / 100)

        rows.sort(key=lambda x: -x[6])
        for ln, nn, gl, mkt, lc, bn, d in rows:
            print(f"{ln:<8}{nn:>4}  {gl:>6.1f}%  {mkt:>7.1f}%  {lc:>7.1f}%  {bn:>9}  {d:>+7.1f}")

        n = total["n"]
        print("-" * 96)
        print(f"{'整体':<8}{n:>4}  {total['gl']/n*100:>6.1f}%  {total['mkt']/n*100:>7.1f}%  {total['lc']/n*100:>7.1f}%  "
              f"{'分联赛最优':>9}  {total['best']/n*100 - total['lc']/n*100:>+7.1f}")
        print(f"\n  全局 λc 融合命中率 : {total['lc']/n*100:.1f}%")
        print(f"  分联赛选最优上界   : {total['best']/n*100:.1f}%  (含过拟合，实际会打折)")


if __name__ == "__main__":
    asyncio.run(main())
