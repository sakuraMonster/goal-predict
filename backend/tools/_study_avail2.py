"""数据可用性(宽松口径): goal_line 众数即可算开盘/收盘线, 不要求 over+under 齐全
同时检查: 快照数据的时间覆盖(旧比赛是否有开盘时刻数据)
"""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot

START = datetime(2026, 7, 11, 12, 0, 0)
END = datetime(2026, 8, 11, 12, 0, 0)


def gl_mode(records):
    gls = [r[0] for r in records if r[0] is not None]
    if not gls:
        return None
    return Counter(gls).most_common(1)[0][0]


async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Prediction).options(joinedload(Prediction.match))
            .where(and_(Prediction.kickoff_time >= START, Prediction.kickoff_time < END))
        )
        preds = r.unique().scalars().all()

        have, miss = {}, {}
        rows = []
        for p in preds:
            m = p.match
            if not m:
                continue
            r2 = await db.execute(
                select(OddsSnapshot).where(OddsSnapshot.match_id == m.id).order_by(OddsSnapshot.snapshot_time.asc())
            )
            snaps = list(r2.scalars().all())
            lg = m.league.name_zh if m.league else "?"
            if not snaps:
                miss.setdefault(lg, 0); miss[lg] += 1
                continue
            times = sorted(set(s.snapshot_time for s in snaps))
            opening_gl = gl_mode([(s.goal_line, 0, 0, 0) for s in snaps if s.snapshot_time == times[0]])
            closing_gl = gl_mode([(s.goal_line, 0, 0, 0) for s in snaps if s.snapshot_time == times[-1]])
            # 判断: 首快照距开球多久, 末快照距开球多久
            if opening_gl is not None and closing_gl is not None:
                have.setdefault(lg, 0); have[lg] += 1
                rows.append((m.id, lg, m.kickoff_time, times[0], times[-1], opening_gl, closing_gl,
                             (m.kickoff_time - times[0]).total_seconds() / 3600,
                             (m.kickoff_time - times[-1]).total_seconds() / 3600,
                             p.actual_total_goals))
            else:
                miss.setdefault(lg, 0); miss[lg] += 1

        total = sum(have.values()) + sum(miss.values())
        print(f"总场次 {total} | 可用(宽松口径) {sum(have.values())} | 不可用 {sum(miss.values())}")
        print(f"\n可用联赛分布:")
        for lg, c in sorted(have.items(), key=lambda x: -x[1]):
            print(f"  {lg:<8} {c}")
        print(f"\n不可用联赛分布:")
        for lg, c in sorted(miss.items(), key=lambda x: -x[1]):
            print(f"  {lg:<8} {c}")

        print(f"\n可用场次明细 (id 联赛 开球 首快照 末快照 开盘线 收盘线 首-开球h 末-开球h 实际):")
        for row in sorted(rows, key=lambda x: x[2]):
            print(f"  {row[0]:<7}{row[1]:<8}{row[2]:%m-%d %H:%M} {row[3]:%m-%d %H:%M}~{row[4]:%m-%d %H:%M} "
                  f"open={row[5]} close={row[6]} hdrift={row[7]:>8.1f} pre={row[8]:>6.1f} act={row[9]}")


asyncio.run(main())
