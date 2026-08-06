import asyncio, sys, os
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from datetime import datetime, timedelta, date
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction, League

OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_u2.txt"

async def main():
    lines = []
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
                unsettled.append((kt, m.home_team_name, m.away_team_name, lg, gl, snap, m.status))

        lines.append("Unsettled: {}".format(len(unsettled)))
        lines.append("")
        for kt, home, away, lg, gl, snap, st in unsettled:
            lines.append("{} | {} vs {} | {} | lam_c={} SNAP={} | {}".format(kt, home, away, lg, gl, snap, st))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Done ->", OUT)

asyncio.run(main())
