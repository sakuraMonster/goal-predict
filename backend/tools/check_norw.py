"""检查 挪超 受影响的比赛"""
import asyncio, sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import joinedload
from app.db.database import engine
from app.db.models import Prediction, Match, TeamSeasonStats, Team
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_b import ModelB

async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 7, 28, 12, 0, 0)
    end = datetime(2026, 8, 4, 12, 0, 0)

    async with sf() as db:
        r0 = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.home_team),
                     joinedload(Prediction.match).joinedload(Match.away_team),
                     joinedload(Prediction.match).joinedload(Match.league))
            .where(Prediction.kickoff_time >= start, Prediction.kickoff_time < end,
                   Prediction.actual_home_score.isnot(None))
            .order_by(Prediction.kickoff_time)
        )
        preds = list(r0.unique().scalars().all())
        norw = [p for p in preds if p.league and p.league.name_zh == "挪超"]

        feat_b = FeatureEngineerB(db)
        mb = ModelB()

        for p in norw:
            m = p.match
            if not m: continue
            mid = m.id
            home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
            actual = (p.actual_home_score or 0) + (p.actual_away_score or 0)
            score = f"{p.actual_home_score or 0}:{p.actual_away_score or 0}"

            feats_df = await feat_b.extract_features(mid, None)
            if feats_df.empty: continue
            feats = feats_df.iloc[0].to_dict()

            # xG 值
            hxg = feats.get("home_xG", 0)
            axg = feats.get("away_xG", 0)
            h_eff = feats.get("home_attacking_efficiency", 0)
            a_eff = feats.get("away_attacking_efficiency", 0)

            raw_lam = mb.model.predict(mb._scale_features(feats_df.reindex(columns=mb._feature_names, fill_value=0.0)))[0]
            raw_lam = max(raw_lam, 0.1); raw_lam = min(raw_lam, 8.0)

            # SNAP
            # With calibration: ×1.10 for 挪超
            adj_lam = raw_lam * 1.10
            eg = round(adj_lam, 10)
            frac = eg - math.floor(eg)
            if frac < 0.10: eff = math.floor(eg)
            elif frac > 0.90: eff = math.ceil(eg)
            else: eff = eg
            dists = sorted([(abs(eff - k), k) for k in range(5)])
            top2 = sorted([dists[0][1], dists[1][1]])
            act = min(actual, 4)
            snap = "V" if act in top2 else "X"

            print(f"  MID={mid:5d} {snap} {home:12s} vs {away:12s} {score} T={actual} | "
                  f"raw_λ={raw_lam:.2f}→adj={adj_lam:.2f} top2={top2[0]}/{top2[1]} | "
                  f"h_xG={hxg:.2f} a_xG={axg:.2f} h_eff={h_eff:.1f} a_eff={a_eff:.1f}")

asyncio.run(main())
