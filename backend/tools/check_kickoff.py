"""检查比赛开赛时间"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.db.database import async_session
from sqlalchemy import select
from app.db.models import Match

async def main():
    async with async_session() as db:
        r = await db.execute(select(Match).order_by(Match.kickoff_time.desc()).limit(5))
        for m in r.scalars():
            print(f"id={m.id} kickoff={m.kickoff_time} type={type(m.kickoff_time)} jc_id={m.jc_match_id} home={m.home_team_name} away={m.away_team_name}")

asyncio.run(main())
