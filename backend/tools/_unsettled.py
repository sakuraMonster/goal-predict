import asyncio, sys, os
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from datetime import datetime, timedelta, date
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction, League

async def main():
    async with async_session() as db:
        result = await db.execute(select(League.id, League.name_zh))
        league_names = {row[0]: row[1] for row in result}

        today = date.today()
        start = today - timedelta(days=30)
        qs = datetime(start.year, start.month, start.day, 12)
        qe = datetime(today.year, today.month, today.day, 12) + timedelta(days=1)

        result = await db.execute(
            select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time >= qs, Match.kickoff_time < qe)
            .order_by(Match.kickoff_time)
        )
        unsettled = []
        for m in result.unique().scalars().all():
            pr = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = pr.scalar_one_or_none()
            if pred and pred.actual_total_goals is None:
                lg = league_names.get(m.league_id, "?")
                kt = m.kickoff_time.strftime("%m-%d %H:%M") if m.kickoff_time else "?"
                gl = pred.expected_goals_c
                snap = pred.snap_top2_c
                hs = m.home_score if m.home_score is not None else "?"
                as_ = m.away_score if m.away_score is not None else "?"
                unsettled.append((kt, m.home_team_name, m.away_team_name, lg, gl, snap, hs, as_, m.status))

        print(f"未结算场次: {len(unsettled)}")
        print("")
        for kt, home, away, lg, gl, snap, hs, as_, st in unsettled:
            print(f"{kt} | {home or '?'} vs {away or '?'} | {lg} | lam_c={gl} SNAP={snap} | score={hs}:{as_} | {st}")

asyncio.run(main())
