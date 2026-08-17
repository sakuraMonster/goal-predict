"""诊断：Model C 的 λ_c 相比市场盘口 goal_line 是否增值（近30天已结算）"""
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

        print(f"近30天已结算: {len(settled)} 场\n")

        agg = defaultdict(lambda: {"n": 0, "gl_hit": 0, "lc_hit": 0, "gl_err": 0.0, "lc_err": 0.0})
        overall = {"n": 0, "gl_hit": 0, "lc_hit": 0, "gl_err": 0.0, "lc_err": 0.0}

        for p in settled:
            tg = (p.actual_home_score or 0) + (p.actual_away_score or 0)
            ln = p.match.league.name_zh if (p.match and p.match.league) else "未知"

            try:
                features_df = await feat_engine.extract_features(p.match_id)
                if features_df.empty:
                    continue
                features = features_df.iloc[0].to_dict()
                gl = float(features.get("goal_line_market", 0) or 0)
                if gl < 0.5:
                    continue
                lc = model_c.predict(features, ln)["expected_goals"]

                gl_h = hit(tg, snap_top2(gl))
                lc_h = hit(tg, snap_top2(lc))

                overall["n"] += 1
                overall["gl_hit"] += gl_h
                overall["lc_hit"] += lc_h
                overall["gl_err"] += abs(gl - tg)
                overall["lc_err"] += abs(lc - tg)

                a = agg[ln]
                a["n"] += 1
                a["gl_hit"] += gl_h
                a["lc_hit"] += lc_h
                a["gl_err"] += abs(gl - tg)
                a["lc_err"] += abs(lc - tg)
            except Exception as e:
                pass

        n = overall["n"]
        print("=" * 100)
        print(f"{'联赛':<8}{'n':>4}  {'盘口命中':>8}  {'λc命中':>8}  {'差值':>7}  {'盘口MAE':>8}  {'λc MAE':>8}  {'MAE差':>7}")
        print("=" * 100)
        rows = []
        for ln, a in agg.items():
            if a["n"] < 3:
                continue
            gl_h = a["gl_hit"] / a["n"] * 100
            lc_h = a["lc_hit"] / a["n"] * 100
            gl_err = a["gl_err"] / a["n"]
            lc_err = a["lc_err"] / a["n"]
            rows.append((ln, a["n"], gl_h, lc_h, lc_h - gl_h, gl_err, lc_err, lc_err - gl_err))
        rows.sort(key=lambda x: -x[2])
        for ln, nn, gl_h, lc_h, d_hit, gl_err, lc_err, d_err in rows:
            print(f"{ln:<8}{nn:>4}  {gl_h:>7.1f}%  {lc_h:>7.1f}%  {d_hit:>+6.1f}  {gl_err:>8.2f}  {lc_err:>8.2f}  {d_err:>+6.2f}")

        print("-" * 100)
        print(f"{'整体':<8}{n:>4}  {overall['gl_hit']/n*100:>7.1f}%  {overall['lc_hit']/n*100:>7.1f}%  "
              f"{(overall['lc_hit']-overall['gl_hit'])/n*100:>+6.1f}  {overall['gl_err']/n:>8.2f}  {overall['lc_err']/n:>8.2f}  "
              f"{(overall['lc_err']-overall['gl_err'])/n:>+6.2f}")


if __name__ == "__main__":
    asyncio.run(main())
