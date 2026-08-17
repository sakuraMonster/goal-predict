"""分解 Model C 负增值来源：盘口 → market侧(calib+adj) → 融合后(λc) 逐层命中率"""
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
                gl = res["detail"]["goal_line"]
                lm = res["detail"]["lambda_market"]
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

        print("=" * 104)
        print(f"{'联赛':<8}{'n':>4}  {'盘口':>7}  {'market侧':>8}  {'λc融合':>8}  {'mkt-盘口':>9}  {'λc-mkt':>8}")
        print("=" * 104)
        rows = []
        for ln, a in agg.items():
            if a["n"] < 4:
                continue
            gl = a["gl"] / a["n"] * 100
            mkt = a["mkt"] / a["n"] * 100
            lc = a["lc"] / a["n"] * 100
            rows.append((ln, a["n"], gl, mkt, lc, mkt - gl, lc - mkt))
        # 按 mkt-盘口 升序（负增值最严重的在前）
        rows.sort(key=lambda x: x[5])
        for ln, nn, gl, mkt, lc, d1, d2 in rows:
            print(f"{ln:<8}{nn:>4}  {gl:>6.1f}%  {mkt:>7.1f}%  {lc:>7.1f}%  {d1:>+8.1f}  {d2:>+7.1f}")

        # 整体
        t = {"n": 0, "gl": 0, "mkt": 0, "lc": 0}
        for a in agg.values():
            for k in ("n", "gl", "mkt", "lc"):
                t[k] += a[k]
        n = t["n"]
        print("-" * 104)
        print(f"{'整体':<8}{n:>4}  {t['gl']/n*100:>6.1f}%  {t['mkt']/n*100:>7.1f}%  {t['lc']/n*100:>7.1f}%  "
              f"{(t['mkt']-t['gl'])/n*100:>+8.1f}  {(t['lc']-t['mkt'])/n*100:>+7.1f}")


if __name__ == "__main__":
    asyncio.run(main())
