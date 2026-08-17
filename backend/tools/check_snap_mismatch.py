"""排查周日004比赛的 SNAP 与预期进球不一致问题"""
import sys, os, asyncio, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Prediction, League


async def main():
    async with async_session() as db:
        rq = await db.execute(
            select(Match).where(Match.match_num.like("%周日004%"))
        )
        matches = list(rq.scalars().all())
        if not matches:
            # 模糊查所有含004的
            rq2 = await db.execute(
                select(Match).where(Match.match_num.like("%004%"))
                .order_by(Match.kickoff_time)
            )
            matches = list(rq2.scalars().all())
            print(f"周日004 未命中，相近编号 {len(matches)} 场:")
        for m in matches:
            lg = None
            if m.league_id:
                lq = await db.execute(select(League.name_zh).where(League.id == m.league_id))
                lg = lq.scalar_one_or_none()
            pr = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            p = pr.scalar_one_or_none()
            print(f"\n=== 比赛 {m.id} [{m.match_num}] {m.home_team_name} vs {m.away_team_name} "
                  f"({lg}) kickoff={m.kickoff_time} ===")
            if not p:
                print("  无 Prediction 记录")
                continue
            print(f"  expected_goals(B)={p.expected_goals} snap_top2(B)={p.snap_top2}")
            print(f"  expected_goals_c={p.expected_goals_c} snap_top2_c={p.snap_top2_c}")
            print(f"  expected_goals_d={p.expected_goals_d} snap_top2_d={p.snap_top2_d}")


asyncio.run(main())
