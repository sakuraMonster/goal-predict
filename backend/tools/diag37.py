"""诊断库普斯 vs 沙巴巴库 (match id=37)"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import Match, Team, TeamSeasonStats, OddsSnapshot, TeamAlias

async def main():
    async with async_session() as db:
        from sqlalchemy import select
        m = (await db.execute(select(Match).where(Match.id == 37))).scalar_one_or_none()
        if not m: print("Match 37 not found"); return
        
        print(f"=== 比赛 id=37 ===")
        print(f"jc_id={m.jc_match_id}  sm_fx={m.sportmonks_fixture_id}  swapped={m.is_swapped}")
        print(f"主: {m.home_team_name}(id={m.home_team_id})  客: {m.away_team_name}(id={m.away_team_id})")
        
        # 球队信息
        for tid, side in [(m.home_team_id,"主"),(m.away_team_id,"客")]:
            t = (await db.execute(select(Team).where(Team.id==tid))).scalar_one_or_none()
            if t:
                print(f"\n  {side}队: id={t.id} sm_id={t.sportmonks_id} zh={t.name_zh} en={t.name_en}")
                a = (await db.execute(select(TeamAlias).where(TeamAlias.team_id==tid))).scalars().all()
                print(f"    别名: {[(x.alias_name, x.source) for x in a]}")
                
                # 赛季统计
                s = (await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id==tid))).scalars().all()
                for ss in s:
                    print(f"    统计: season={ss.season} P={ss.played} W={ss.wins} D={ss.draws} L={ss.losses} GF={ss.goals_for} GA={ss.goals_against} form={ss.form} recent={ss.recent_matches[:3] if ss.recent_matches else '-'}")
        
        # 赔率
        odds = (await db.execute(select(OddsSnapshot).where(OddsSnapshot.match_id==37))).scalars().all()
        print(f"\n  赔率: {len(odds)} 条")

asyncio.run(main())
