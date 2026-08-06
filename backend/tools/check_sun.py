import asyncio,os,sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv;load_dotenv()
from app.db.database import async_session
from app.db.models import Match, OddsSnapshot
from sqlalchemy import select, func

async def main():
    async with async_session() as db:
        for num in ["周三006","周日012","周日013","周日014","周六012","周六013","周六014"]:
            r=await db.execute(select(Match).where(Match.match_num==num))
            ms=r.scalars().all()
            for m in ms:
                odds=await db.scalar(select(func.count(OddsSnapshot.id)).where(OddsSnapshot.match_id==m.id)) or 0
                h=(m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
                a=(m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
                hsm=m.home_team.sportmonks_id if m.home_team else "?"
                asm=m.away_team.sportmonks_id if m.away_team else "?"
                print(f"ID={m.id} {num} {m.kickoff_time} {h}(SM={hsm}) vs {a}(SM={asm}) fx={m.sportmonks_fixture_id} odds={odds}")
asyncio.run(main())
