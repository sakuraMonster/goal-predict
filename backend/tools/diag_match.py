"""诊断库普斯 vs 沙巴巴库 比赛数据"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import Match, Team, TeamSeasonStats, OddsSnapshot, TeamAlias
from sqlalchemy import select

async def main():
    async with async_session() as db:
        # 查找比赛
        result = await db.execute(
            select(Match).where(
                (Match.home_team_name.like("%库普%")) | (Match.away_team_name.like("%库普%")) |
                (Match.home_team_name.like("%沙巴%")) | (Match.away_team_name.like("%沙巴%")) |
                (Match.home_team_name.like("%KuPS%")) | (Match.away_team_name.like("%KuPS%"))
            )
        )
        matches = result.scalars().all()
        print("=== 比赛记录 ===")
        for m in matches:
            print(f"  id={m.id} jc_id={m.jc_match_id} sm_fixture={m.sportmonks_fixture_id}")
            print(f"  {m.home_team_name}(id={m.home_team_id}) vs {m.away_team_name}(id={m.away_team_id})")
            print(f"  kickoff={m.kickoff_time} status={m.status} handicap={m.handicap_line} swapped={m.is_swapped}")
            
            # 球队信息
            for tid, side in [(m.home_team_id, "主"), (m.away_team_id, "客")]:
                if tid:
                    tr = await db.execute(select(Team).where(Team.id == tid))
                    t = tr.scalar_one_or_none()
                    if t:
                        print(f"  {side}队: zh={t.name_zh} en={t.name_en} sm_id={t.sportmonks_id} short={t.short_en}")
                        ar = await db.execute(select(TeamAlias).where(TeamAlias.team_id == tid))
                        aliases = [a.alias_name for a in ar.scalars().all()]
                        print(f"    别名: {aliases}")
            
            # 赔率
            orr = await db.execute(select(OddsSnapshot).where(OddsSnapshot.match_id == m.id).limit(5))
            odds = orr.scalars().all()
            print(f"  赔率快照: {len(odds)} 条")
            for o in odds[:3]:
                print(f"    bookmaker={o.bookmaker} time={o.snapshot_time} 1x2={o.home_win}/{o.draw}/{o.away_win} hcp={o.handicap_home}/{o.handicap_line}/{o.handicap_away}")
            
            # 球队赛季统计
            for tid in [m.home_team_id, m.away_team_id]:
                if tid:
                    sr = await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid).limit(2))
                    stats = sr.scalars().all()
                    for s in stats:
                        print(f"  统计(team_id={tid}): season={s.season} played={s.played} w={s.wins} d={s.draws} l={s.losses} gf={s.goals_for} ga={s.goals_against} form={s.form}")
            
            print()

asyncio.run(main())
