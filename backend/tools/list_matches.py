import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import Match, Team, TeamSeasonStats, OddsSnapshot

async def main():
    async with async_session() as db:
        # 搜索所有最近比赛
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(days=14)
        from sqlalchemy import select
        r = await db.execute(select(Match).where(Match.kickoff_time >= cutoff).order_by(Match.kickoff_time.desc()))
        matches = r.scalars().all()
        print(f"近14天共 {len(matches)} 场比赛")
        for m in matches:
            h = await db.execute(select(Team).where(Team.id == m.home_team_id))
            a = await db.execute(select(Team).where(Team.id == m.away_team_id))
            ht = h.scalar_one_or_none()
            at = a.scalar_one_or_none()
            o = await db.execute(select(OddsSnapshot).where(OddsSnapshot.match_id == m.id).limit(1))
            odds_count = len(o.scalars().all())
            print(f"  id={m.id} sm_fx={m.sportmonks_fixture_id} {ht.name_zh if ht else '?'}({m.home_team_id}) vs {at.name_zh if at else '?'}({m.away_team_id}) odds={odds_count} swapped={m.is_swapped}")

asyncio.run(main())
