"""检查雷达图原始数据"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import Team, TeamSeasonStats, HeadToHead, Match
from sqlalchemy import select
from datetime import datetime, timedelta

async def main():
    async with async_session() as db:
        m = (await db.execute(select(Match).where(Match.id==37))).scalar_one()
        
        for tid, side in [(m.home_team_id,"主"),(m.away_team_id,"客")]:
            t = (await db.execute(select(Team).where(Team.id==tid))).scalar_one()
            s = (await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id==tid).order_by(TeamSeasonStats.season.desc()).limit(1))).scalar_one_or_none()
            
            print(f"\n=== {t.name_zh} (id={tid}, sm_id={t.sportmonks_id}) ===")
            if s:
                print(f"  season={s.season}")
                print(f"  基础: played={s.played} wins={s.wins} draws={s.draws} losses={s.losses}")
                print(f"  进球: GF={s.goals_for} GA={s.goals_against}")
                print(f"  高级: possession={s.avg_possession} xG={s.xG} xGA={s.xGA}")
                print(f"  其他: clean_sheets={s.clean_sheets} failed_to_score={s.failed_to_score}")
                print(f"  form={s.form}")
            else:
                print(f"  TeamSeasonStats: NULL (无数据!)")
        
        # H2H
        h2hs = (await db.execute(select(HeadToHead).where(
            ((HeadToHead.home_team_id==146)&(HeadToHead.away_team_id==125)) |
            ((HeadToHead.home_team_id==125)&(HeadToHead.away_team_id==146))
        ).order_by(HeadToHead.match_date.desc()))).scalars().all()
        print(f"\nH2H: {len(h2hs)} 条")
        for h in h2hs[:3]:
            print(f"  {h.match_date}: score={h.home_score}:{h.away_score} home_stats_keys={list(h.home_stats.keys()) if h.home_stats else 'None'}")

asyncio.run(main())
