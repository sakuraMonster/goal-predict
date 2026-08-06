import asyncio, sys
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')
from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Match, Team, OddsSnapshot, HeadToHead

async def main():
    async with async_session() as db:
        for mid in [15470, 15471]:
            m = await db.get(Match, mid)
            if not m: continue
            
            print(f'\n=== {m.match_num} {m.home_team_name} vs {m.away_team_name} ===')
            print(f'  match.id={m.id} league_id={m.league_id}')
            print(f'  home_team_id={m.home_team_id} away_team_id={m.away_team_id}')
            
            # 检查 Team 表
            ht = await db.get(Team, m.home_team_id)
            at = await db.get(Team, m.away_team_id)
            if ht:
                print(f'  主队Team: id={ht.id} sportmonks_id={ht.sportmonks_id} name_zh={ht.name_zh} name_en={ht.name_en}')
            else:
                print(f'  主队Team: NOT FOUND in DB!')
            if at:
                print(f'  客队Team: id={at.id} sportmonks_id={at.sportmonks_id} name_zh={at.name_zh} name_en={at.name_en}')
            else:
                print(f'  客队Team: NOT FOUND in DB!')
            
            # 赔率
            oc = await db.execute(select(func.count(OddsSnapshot.id)).where(OddsSnapshot.match_id==mid))
            odds_cnt = oc.scalar()
            print(f'  赔率记录数: {odds_cnt}')
            
            if odds_cnt == 0 and m.sportmonks_fixture_id:
                print(f'  sportmonks_fixture_id={m.sportmonks_fixture_id}')
            
            # H2H
            hc = await db.execute(select(func.count(HeadToHead.id)).where(
                ((HeadToHead.home_team_id==m.home_team_id)&(HeadToHead.away_team_id==m.away_team_id))|
                ((HeadToHead.home_team_id==m.away_team_id)&(HeadToHead.away_team_id==m.home_team_id))
            ))
            print(f'  H2H记录数: {hc.scalar()}')

asyncio.run(main())
