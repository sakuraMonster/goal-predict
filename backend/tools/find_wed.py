import asyncio,os,sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv;load_dotenv()
from app.db.database import async_session
from app.db.models import Match
from sqlalchemy import select
async def main():
    async with async_session() as db:
        r=await db.execute(select(Match).where(Match.match_num.like('%006')))
        for m in r.scalars().all():
            h=m.home_team.name_zh if m.home_team else m.home_team_name
            a=m.away_team.name_zh if m.away_team else m.away_team_name
            lg=m.league.name_zh if m.league else "?"
            print(f"{m.match_num} ID={m.id} {h} vs {a} league={lg} kt={m.kickoff_time} fx={m.sportmonks_fixture_id}")
asyncio.run(main())
