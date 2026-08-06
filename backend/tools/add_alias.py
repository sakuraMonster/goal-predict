import asyncio,os,sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv;load_dotenv()
from app.db.database import async_session
from app.db.models import TeamAlias
from sqlalchemy import select
async def main():
    async with async_session() as db:
        exist=await db.execute(select(TeamAlias).where(TeamAlias.team_id==274,TeamAlias.alias_name=='赫尔火花'))
        if not exist.scalar_one_or_none():
            db.add(TeamAlias(team_id=274,alias_name='赫尔火花',source='manual'))
            await db.commit()
            print('done: added 赫尔火花')
        else:
            print('already exists')
asyncio.run(main())
