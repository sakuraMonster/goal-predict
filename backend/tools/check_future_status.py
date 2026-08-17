"""检查未来比赛的 status 分布与 kickoff 分布"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Match


async def main():
    now = datetime.now()
    async with async_session() as db:
        # 未来比赛（kickoff >= now - 1h），不带 status 过滤
        rq = await db.execute(
            select(
                Match.status,
                func.count(Match.id),
                func.min(Match.kickoff_time),
                func.max(Match.kickoff_time),
            ).where(Match.kickoff_time >= now - timedelta(hours=1))
            .group_by(Match.status)
        )
        print("kickoff >= 现在-1h 的 status 分布:")
        for row in rq:
            print(f"  status={row[0]}: {row[1]} 场, kickoff范围 {row[2]} ~ {row[3]}")

        # 未来7天（不带 status 过滤）
        future_start = now.replace(hour=12, minute=0, second=0, microsecond=0)
        future_end = now + timedelta(days=7)
        rq2 = await db.execute(
            select(func.count(Match.id)).where(
                Match.kickoff_time >= future_start,
                Match.kickoff_time <= future_end,
            )
        )
        print(f"\nkickoff >= {future_start} 且 <= {future_end}: {rq2.scalar()} 场")

        # 最近的比赛样例
        rq3 = await db.execute(
            select(Match.id, Match.home_team_name, Match.away_team_name,
                   Match.kickoff_time, Match.status, Match.sportmonks_fixture_id)
            .where(Match.kickoff_time >= future_start)
            .order_by(Match.kickoff_time)
            .limit(10)
        )
        print("\n未来比赛样例:")
        for m in rq3:
            print(f"  {m[0]} {m[1]} vs {m[2]} | {m[3]} | status={m[4]} | sm_id={m[5]}")


asyncio.run(main())
