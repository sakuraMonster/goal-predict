"""查询当前无 SportMonks fixture 映射的竞彩赛事（需人工处理的那场）"""
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.db.database import async_session
from app.db.models import Match, Team, League


async def main():
    async with async_session() as db:
        cutoff = datetime.utcnow() - timedelta(days=1)
        result = await db.execute(
            select(Match)
            .options(joinedload(Match.home_team), joinedload(Match.away_team), joinedload(Match.league))
            .where(
                Match.sportmonks_fixture_id.is_(None),
                Match.status == "scheduled",
                Match.kickoff_time >= cutoff,
            )
        )
        matches = result.unique().scalars().all()
        print(f"当前无 fixture 映射的赛事（kickoff >= {cutoff}）: {len(matches)} 场")
        for m in matches:
            ht = m.home_team
            at = m.away_team
            league = m.league.name_zh if m.league else "未知"
            print(
                f"  jc={m.jc_match_id} | {league} | "
                f"{m.home_team_name}(id={m.home_team_id}, sm={ht.sportmonks_id if ht else None}, en={ht.name_en if ht else None}) "
                f"vs {m.away_team_name}(id={m.away_team_id}, sm={at.sportmonks_id if at else None}, en={at.name_en if at else None}) "
                f"| kickoff={m.kickoff_time} | status={m.status} | fx={m.sportmonks_fixture_id}"
            )


if __name__ == "__main__":
    asyncio.run(main())
