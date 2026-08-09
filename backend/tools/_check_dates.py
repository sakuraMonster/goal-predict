import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction

async def main():
    end = datetime(2026, 8, 7, 12, 0, 0)
    start = end - timedelta(days=30)
    async with async_session() as db:
        r = await db.execute(select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time >= start, Match.kickoff_time < end)
            .order_by(Match.kickoff_time))
        for m in r.unique().scalars().all():
            d = m.kickoff_time.strftime("%m-%d") if hasattr(m.kickoff_time, 'strftime') else str(m.kickoff_time)[:10]
            if d not in ("08-04", "08-05"):
                continue
            pr = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            p = pr.scalar_one_or_none()
            lg = m.league.name_zh if m.league else "未知"
            score = p.actual_score if p else "?"
            goals = p.actual_total_goals if p else "?"
            print(f"{d} {m.kickoff_time.strftime('%H:%M')} {m.home_team_name} vs {m.away_team_name} ({lg}) {score}({goals}球)")

asyncio.run(main())
