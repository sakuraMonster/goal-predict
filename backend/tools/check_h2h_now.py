"""检查 H2H 和 Match 数据当前状态"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.db.database import async_session
from sqlalchemy import select
from app.db.models import HeadToHead, Match, Team
from datetime import datetime, timedelta

async def main():
    async with async_session() as db:
        # H2H count
        r = await db.execute(select(HeadToHead))
        h2h = r.scalars().all()
        print(f'H2H records: {len(h2h)}')
        for h in h2h[:5]:
            print(f'  home_id={h.home_team_id} away_id={h.away_team_id} date={h.match_date} h_score={h.home_score} a_score={h.away_score} home_stats={h.home_stats} away_stats={h.away_stats}')
        
        # All matches
        r = await db.execute(select(Match))
        all_m = r.scalars().all()
        print(f'\nAll matches: {len(all_m)}')
        for m in all_m:
            print(f'  id={m.id} home_id={m.home_team_id} away_id={m.away_team_id} kickoff={m.kickoff_time} sm_id={m.sportmonks_fixture_id}')
        
        # Matches within 12h
        cutoff = datetime.utcnow() - timedelta(hours=12)
        r = await db.execute(select(Match).where(Match.kickoff_time >= cutoff))
        recent = r.scalars().all()
        print(f'\nMatches in last 12h: {len(recent)}')
        
        # Teams with sportmonks_id
        r = await db.execute(select(Team.id, Team.name_zh, Team.sportmonks_id))
        teams = r.all()
        print(f'\nTeams: {len(teams)}')
        for t in teams:
            print(f'  id={t[0]} name={t[1]} sm_id={t[2]}')

asyncio.run(main())
