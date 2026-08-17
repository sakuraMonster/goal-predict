"""检查 15547-15570 等比赛在 odds_snapshots 中的快照时间分布"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import OddsSnapshot, Match

async def main():
    async with async_session() as db:
        # 选取几场目标比赛
        ids = [15547, 15548, 15549, 15550, 15552, 15553, 15555, 15566, 15567, 15568, 15570]
        for mid in ids:
            res = await db.execute(
                select(
                    OddsSnapshot.snapshot_time,
                    func.count(OddsSnapshot.id),
                    func.count(OddsSnapshot.goal_line),
                    func.min(OddsSnapshot.goal_line),
                    func.max(OddsSnapshot.goal_line),
                    func.array_agg(func.distinct(OddsSnapshot.bookmaker)),
                ).where(OddsSnapshot.match_id == mid)
                .group_by(OddsSnapshot.snapshot_time)
                .order_by(OddsSnapshot.snapshot_time)
            )
            rows = res.all()
            if not rows:
                print(f"match {mid}: NO odds data")
                continue
            detail = "; ".join(
                f"{t.strftime('%m-%d %H:%M')} n={n} gl_count={gc} gl=[{gl_min}~{gl_max}] bm={bms}"
                for t, n, gc, gl_min, gl_max, bms in rows
            )
            # 查询比赛信息
            mres = await db.execute(select(Match).where(Match.id == mid))
            m = mres.scalar_one_or_none()
            print(f"match {mid} ({m.home_team_name} vs {m.away_team_name}): {detail}")

asyncio.run(main())
