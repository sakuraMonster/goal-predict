"""标记未匹配场次为 cancelled（SM 数据源未收录，跳过，不重复匹配）
场次：jc=2040914 芬超 赫尔辛基火花 vs 坦佩雷山猫 (2026-08-17 23:00)
依据：SM 实测 912/8998 H2H 0 条、08-14~08-20 between 窗口无此场、芬兰联赛无 fixture
"""
import asyncio
from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Match


async def main():
    async with async_session() as db:
        result = await db.execute(
            select(Match).where(Match.jc_match_id == "2040914")
        )
        m = result.scalar_one_or_none()
        if not m:
            print("未找到 jc=2040914")
            return
        print(f"before: jc={m.jc_match_id} {m.home_team_name} vs {m.away_team_name} "
              f"kickoff={m.kickoff_time} status={m.status} fx={m.sportmonks_fixture_id}")
        m.status = "cancelled"
        await db.commit()
        print(f"after : status={m.status}")


if __name__ == "__main__":
    asyncio.run(main())
