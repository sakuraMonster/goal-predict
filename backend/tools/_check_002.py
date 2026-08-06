import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Team, HeadToHead

async def main():
    async with async_session() as db:
        # Final verification
        m = await db.execute(select(Match).where(Match.id == 15470))
        match = m.scalar_one()
        print(f"Match 15470 (周五002): home={match.home_team_id}, away={match.away_team_id}")
        
        r = await db.execute(select(HeadToHead).where(
            ((HeadToHead.home_team_id == match.home_team_id) & (HeadToHead.away_team_id == match.away_team_id)) |
            ((HeadToHead.home_team_id == match.away_team_id) & (HeadToHead.away_team_id == match.home_team_id))
        ).order_by(HeadToHead.match_date.desc()))
        h2h = r.scalars().all()
        print(f"\nH2H records: {len(h2h)}")
        for h in h2h:
            hs = h.home_stats
            if isinstance(hs, str):
                hs = __import__('json').loads(hs)
            print(f"  {h.match_date}: {h.home_team_id} vs {h.away_team_id} {h.home_score}-{h.away_score}")
            if hs:
                print(f"    home_stats: {hs}")
            aws = h.away_stats
            if isinstance(aws, str):
                aws = __import__('json').loads(aws)
            if aws:
                print(f"    away_stats: {aws}")

asyncio.run(main())
