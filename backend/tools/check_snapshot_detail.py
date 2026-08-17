"""检查 15547 最新时刻的快照明细：哪些庄家、哪些线"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import OddsSnapshot

async def main():
    async with async_session() as db:
        for mid in [15547, 15567, 15550]:
            print(f"\n===== match {mid} =====")
            res = await db.execute(
                select(OddsSnapshot).where(OddsSnapshot.match_id == mid)
                .order_by(OddsSnapshot.snapshot_time.desc())
            )
            snaps = res.scalars().all()
            if not snaps:
                print("  NO DATA")
                continue
            latest_t = snaps[0].snapshot_time
            # 按时间分组统计
            from collections import defaultdict
            by_t = defaultdict(list)
            for s in snaps:
                by_t[s.snapshot_time].append(s)
            for t in sorted(by_t.keys()):
                ss = by_t[t]
                lines = sorted(set(s.goal_line for s in ss if s.goal_line is not None))
                bms = sorted(set(s.bookmaker for s in ss))
                print(f"  {t.strftime('%m-%d %H:%M')}: n={len(ss)} lines={lines} bm={bms}")

asyncio.run(main())
