import asyncio, sys, os
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

async def main():
    async with async_session() as db:
        feat = FeatureEngineerB(db)
        mc = ModelC()

        # 光州FC (match_id=10)
        m_result = await db.execute(select(Match).where(Match.id == 10))
        m = m_result.scalar_one_or_none()
        pred_r = await db.execute(select(Prediction).where(Prediction.match_id == 10))
        pred = pred_r.scalar_one_or_none()
        print(f"光州FC: DB lam_c={pred.expected_goals_c} snap_c={pred.snap_top2_c} actual={pred.actual_total_goals}")
        fdf = await feat.extract_features(10)
        rc = mc.predict(fdf.iloc[0].to_dict(), "韩K")
        d = rc["detail"]
        new_snap = snap_top2(rc["expected_goals"])
        old_hit = pred.actual_total_goals in pred.snap_top2_c if pred.actual_total_goals and pred.snap_top2_c else None
        new_hit = pred.actual_total_goals in new_snap
        print(f"  New: lam={rc['expected_goals']} snap={new_snap} | GL={d['goal_line']} drop={d['goal_drop']} drop_adj={d['drop_adj']:.4f} calib={d['calib']}")
        print(f"  Old HIT={old_hit} New HIT={new_hit}")

        # 全北现代 vs 首尔FC (08-01)
        m2_result = await db.execute(select(Match).where(
            Match.home_team_name.like("%全北%"), Match.kickoff_time >= "2026-08-01"
        ))
        for m2 in m2_result.scalars().all():
            pred2_r = await db.execute(select(Prediction).where(Prediction.match_id == m2.id))
            pred2 = pred2_r.scalar_one_or_none()
            print(f"\n{m2.home_team_name} vs {m2.away_team_name} (id={m2.id})")
            print(f"  DB lam_c={pred2.expected_goals_c} snap_c={pred2.snap_top2_c} actual={pred2.actual_total_goals}")
            f2df = await feat.extract_features(m2.id)
            rc2 = mc.predict(f2df.iloc[0].to_dict(), "韩K")
            d2 = rc2["detail"]
            new_snap2 = snap_top2(rc2["expected_goals"])
            oh2 = pred2.actual_total_goals in pred2.snap_top2_c if pred2.actual_total_goals and pred2.snap_top2_c else None
            nh2 = pred2.actual_total_goals in new_snap2
            print(f"  New: lam={rc2['expected_goals']} snap={new_snap2} | GL={d2['goal_line']} drop={d2['goal_drop']} drop_adj={d2['drop_adj']:.4f}")
            print(f"  Old HIT={oh2} New HIT={nh2}")

asyncio.run(main())
