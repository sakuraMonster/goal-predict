import asyncio, sys, json
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import HeadToHead, Match

FUTURE = [15469,15470,15471,15472,15473,15474,15475,15476,15477]

async def main():
    async with async_session() as db:
        for mid in FUTURE:
            m = await db.get(Match, mid)
            hr = await db.execute(select(HeadToHead).where(
                ((HeadToHead.home_team_id==m.home_team_id)&(HeadToHead.away_team_id==m.away_team_id))|
                ((HeadToHead.home_team_id==m.away_team_id)&(HeadToHead.away_team_id==m.home_team_id))
            ))
            records = hr.scalars().all()
            with_stats = [r for r in records if r.home_stats is not None]
            has_keys = []
            for r in with_stats:
                hs = json.loads(r.home_stats) if isinstance(r.home_stats,str) else r.home_stats
                ks = list(hs.keys()) if isinstance(hs,dict) else []
                has_keys.extend(ks)
            has_keys = list(set(has_keys))
            name = f'{m.match_num}'
            print(f'{name}: H2H={len(records)} 有stats={len(with_stats)} keys={has_keys[:6]}')

asyncio.run(main())
