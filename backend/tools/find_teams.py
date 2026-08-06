"""查找波兹南莱赫和里莫的比赛对手"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.db.database import async_session
from sqlalchemy import select
from app.db.models import Match, Team

async def main():
    async with async_session() as db:
        for tid in [292, 293]:
            t = await db.execute(select(Team).where(Team.id == tid))
            team = t.scalar_one_or_none()
            print(f"\n=== 球队 id={tid}: {team.name_zh} (en={team.name_en}) ===")
            
            # 查找这个球队参与的比赛
            r = await db.execute(
                select(Match).where((Match.home_team_id == tid) | (Match.away_team_id == tid))
            )
            for m in r.scalars().all():
                is_home = m.home_team_id == tid
                opp_id = m.away_team_id if is_home else m.home_team_id
                # 获取对手信息
                opp = await db.execute(select(Team).where(Team.id == opp_id))
                opp_team = opp.scalar_one_or_none()
                print(f"  Match id={m.id} jc_id={m.jc_match_id} kickoff={m.kickoff_time}")
                print(f"    {'主' if is_home else '客'}场 vs {opp_team.name_zh} (id={opp_id}, sm_id={opp_team.sportmonks_id})")
                print(f"    SM fixture_id={m.sportmonks_fixture_id}")

asyncio.run(main())
