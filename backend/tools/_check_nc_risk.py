import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB

async def main():
    end = datetime(2026,8,7,12,0,0); start = end - timedelta(days=30)
    async with async_session() as db:
        fc = FeatureEngineerB(db); mc = ModelC()
        r = await db.execute(select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time>=start, Match.kickoff_time<end).order_by(Match.kickoff_time))
        for m in r.unique().scalars().all():
            if (m.league.name_zh if m.league else '') != '挪超': continue
            pr = await db.execute(select(Prediction).where(Prediction.match_id==m.id))
            p = pr.scalar_one_or_none()
            if not p or p.actual_total_goals is None: continue
            fd = await fc.extract_features(m.id)
            if fd.empty: continue
            rc = mc.predict(fd.iloc[0].to_dict(), '挪超')
            risk = rc.get('high_goal_risk')
            d = rc['detail']
            print(f"{m.home_team_name} vs {m.away_team_name}: act={p.actual_total_goals} lam={rc['expected_goals']:.2f} drop={d['goal_drop']:.2f} risk={risk['level'] if risk else 'N/A'}")
            if risk and risk.get('factors'):
                for f in risk['factors']: print(f'  - {f}')

asyncio.run(main())
