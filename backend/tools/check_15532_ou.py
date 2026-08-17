"""检查 15532 早期快照的 OU 字段完整性"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import OddsSnapshot

async def main():
    async with async_session() as db:
        res = await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id == 15532)
            .order_by(OddsSnapshot.snapshot_time)
        )
        snaps = res.scalars().all()
        from collections import defaultdict
        by_t = defaultdict(list)
        for s in snaps:
            by_t[s.snapshot_time].append(s)
        for t in sorted(by_t.keys()):
            ss = by_t[t]
            # 统计有 over/under 的行
            has_ou = [s for s in ss if s.over_odds is not None or s.under_odds is not None]
            both = [s for s in ss if s.over_odds is not None and s.under_odds is not None]
            gls = sorted(set(s.goal_line for s in has_ou if s.goal_line is not None))
            print(f"{t.strftime('%m-%d %H:%M')}: n={len(ss)} has_ou={len(has_ou)} both={len(both)} gls_with_ou={gls}")
            # 打印最早的 few 行
            if t == sorted(by_t.keys())[0]:
                for s in ss[:5]:
                    print(f"    bm={s.bookmaker} gl={s.goal_line} over={s.over_odds} under={s.under_odds} hcp_line={s.handicap_line}")

asyncio.run(main())
