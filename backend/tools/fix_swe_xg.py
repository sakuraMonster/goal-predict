"""修复 瑞典超(league_id=9) 球队 NULL/异常 xG"""
import asyncio, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.db.database import engine
from app.db.models import TeamSeasonStats, Team, Match

async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    LEAGUE_ID = 9  # 瑞典超
    async with sf() as db:
        # 找该联赛所有球队
        r = await db.execute(
            select(Team.id).distinct().join(Match,
                (Match.home_team_id == Team.id) | (Match.away_team_id == Team.id)
            ).where(Match.league_id == LEAGUE_ID)
        )
        team_ids = {row[0] for row in r}

        fixed = 0
        for tid in team_ids:
            tr = await db.execute(select(Team).where(Team.id == tid))
            t = tr.scalar_one_or_none()
            tname = t.name_zh if t else f"id={tid}"

            r2 = await db.execute(
                select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid)
            )
            for s in r2.scalars():
                need = False
                if s.goals_for and s.goals_for > 0:
                    need_xg = (s.xG is None or
                               (s.played > 0 and float(s.xG)/s.played < 0.3) or
                               (s.xG > s.goals_for * 1.1))
                    if need_xg:
                        s.xG = float(s.goals_for)
                        need = True
                if s.goals_against and s.goals_against > 0:
                    need_xga = (s.xGA is None or
                                (s.played > 0 and float(s.xGA)/s.played < 0.3) or
                                (s.xGA > s.goals_against * 1.1))
                    if need_xga:
                        s.xGA = float(s.goals_against)
                if need:
                    fixed += 1
                    xg_pg = s.xG / s.played if s.played > 0 else 0
                    print(f"  {tname:14s} stats_id={s.id:5d} played={s.played:3d} xG:→{s.xG:.1f}({xg_pg:.2f}/g)")

        await db.flush()
        await db.commit()
        print(f"\n总计修复 {fixed} 条记录")

asyncio.run(main())
