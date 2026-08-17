"""修复：为韩K联赛补 '韩职' 别名，使竞彩网同步能匹配到韩K联赛"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import LeagueAlias, League, Match


async def main():
    async with async_session() as db:
        # 韩K联赛
        r = await db.execute(select(League).where(League.id == 6))
        league = r.scalar_one_or_none()
        if not league:
            print("韩K联赛不存在")
            return

        # 检查是否已有 '韩职' 别名
        r2 = await db.execute(
            select(LeagueAlias).where(LeagueAlias.league_id == 6, LeagueAlias.alias_name == "韩职")
        )
        if r2.scalar_one_or_none():
            print("'韩职' 别名已存在")
        else:
            db.add(LeagueAlias(league_id=6, alias_name="韩职", source="sporttery.cn", is_primary=False))
            await db.commit()
            print("已添加别名: 韩职 → 韩K(6)")

        # 同步历史上未匹配的韩K比赛（venue='韩职' 且 league_id=None）
        r3 = await db.execute(
            select(Match).where(Match.league_id.is_(None), Match.venue == "韩职")
        )
        matches = r3.scalars().all()
        print(f"待修复的韩K比赛: {len(matches)} 场")
        for m in matches:
            m.league_id = 6
            print(f"  {m.id}: {m.home_team_name} vs {m.away_team_name} → league_id=6")
        if matches:
            await db.commit()
            print("历史比赛已回填 league_id=6")

        # 验证
        r4 = await db.execute(select(Match).where(Match.id == 15532))
        m = r4.scalar_one_or_none()
        print(f"\n验证 15532: league_id={m.league_id if m else None}")


asyncio.run(main())
