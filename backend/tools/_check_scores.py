import asyncio, sys
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Prediction

async def main():
    async with async_session() as db:
        for mid in [15463,15464,15465,15466,15467,15468]:
            mr = await db.execute(select(Match).where(Match.id==mid))
            m = mr.scalar_one_or_none()
            if m:
                print(f'ID={mid} {m.match_num}: home_score={m.home_score} away_score={m.away_score} half={m.half_home_score}:{m.half_away_score}')
            else:
                print(f'ID={mid}: NOT FOUND')
            
            pr = await db.execute(select(Prediction).where(Prediction.match_id==mid))
            preds = pr.scalars().all()
            print(f'  Predictions: {len(preds)} records')
            for p in preds:
                print(f'    actual: {p.actual_home_score}:{p.actual_away_score} result_spf={p.result_spf}')

asyncio.run(main())
