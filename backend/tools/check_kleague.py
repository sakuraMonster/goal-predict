"""检查韩K联赛在League表中的记录及匹配情况"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, or_
from app.db.database import async_session
from app.db.models import League, LeagueAlias, Match


async def main():
    async with async_session() as db:
        # 所有含"韩"/"K"的联赛
        r = await db.execute(select(League))
        leagues = r.scalars().all()
        print("=== League 表 ===")
        for l in leagues:
            print(f"  id={l.id} zh={l.name_zh!r} en={l.name_en!r} sm_id={getattr(l, 'sportmonks_id', None)}")

        r2 = await db.execute(select(LeagueAlias))
        aliases = r2.scalars().all()
        print("\n=== LeagueAlias 表 ===")
        for a in aliases:
            print(f"  id={a.id} league_id={a.league_id} alias={a.alias_name!r}")

        # 韩K比赛
        r3 = await db.execute(
            select(Match).where(
                or_(Match.home_team_name.contains("现代"), Match.home_team_name.contains("FC"))
            ).limit(20)
        )
        print("\n=== 韩K相关比赛 ===")
        for m in r3.scalars().all():
            print(f"  id={m.id} league_id={m.league_id} venue={m.venue!r} 主={m.home_team_name} 客={m.away_team_name}")


asyncio.run(main())
