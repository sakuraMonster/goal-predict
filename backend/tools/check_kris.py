import asyncio,os,sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv;load_dotenv()
from app.db.database import async_session
from app.db.models import Team,TeamAlias,Match
from sqlalchemy import select
async def main():
    async with async_session() as db:
        r=await db.execute(select(Team).where(Team.name_zh.contains('克里斯')))
        for t in r.scalars().all():
            mr=await db.execute(select(Match).where((Match.home_team_id==t.id)|(Match.away_team_id==t.id)).order_by(Match.kickoff_time.desc()).limit(3))
            ms=mr.scalars().all()
            ar=await db.execute(select(TeamAlias).where(TeamAlias.team_id==t.id))
            print(f'id={t.id} name={t.name_zh} en={t.name_en} SM={t.sportmonks_id} aliases={[a.alias_name for a in ar.scalars().all()]} matches={len(ms)}')
asyncio.run(main())
