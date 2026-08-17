"""数据可用性检查: 30 天已结算比赛里, 有多少场能算出开盘共识线/收盘共识线
判断"初盘锚定"实证研究的样本量是否足够
"""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from sqlalchemy import select, and_, func
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot

START = datetime(2026, 7, 11, 12, 0, 0)
END = datetime(2026, 8, 11, 12, 0, 0)


def main_line_at(records):
    """records: [(gl, ov, un, bm)] → 主盘线众数 (与 features_base 一致)"""
    bm_main = {}
    for gl, ov, un, bm in records:
        if ov is None or un is None:
            continue
        diff = abs(ov - un)
        if bm not in bm_main or diff < bm_main[bm][1]:
            bm_main[bm] = (gl, diff)
    gls = [v[0] for v in bm_main.values()]
    if not gls:
        return None, {}
    cnt = Counter(gls)
    return cnt.most_common(1)[0][0], dict(cnt)


async def main():
    async with async_session() as db:
        # 已结算比赛
        r = await db.execute(
            select(Prediction).options(joinedload(Prediction.match))
            .where(and_(Prediction.kickoff_time >= START, Prediction.kickoff_time < END))
        )
        preds = r.unique().scalars().all()
        print(f"已结算比赛共 {len(preds)} 场")

        stats = {"total": 0, "no_snap": 0, "1_time": 0, "can_open": 0, "can_close": 0, "both": 0}
        lg_have = {}
        lg_miss = {}
        rows_miss = []
        for p in preds:
            m = p.match
            if not m:
                continue
            stats["total"] += 1
            r2 = await db.execute(
                select(OddsSnapshot).where(OddsSnapshot.match_id == m.id).order_by(OddsSnapshot.snapshot_time.asc())
            )
            snaps = list(r2.scalars().all())
            lg = m.league.name_zh if m.league else "?"
            if not snaps:
                stats["no_snap"] += 1
                lg_miss.setdefault(lg, 0)
                lg_miss[lg] += 1
                continue
            times = sorted(set(s.snapshot_time for s in snaps))
            if len(times) < 2:
                stats["1_time"] += 1
                lg_miss.setdefault(lg, 0)
                lg_miss[lg] += 1
                continue

            # 开盘共识线: 第一个 over+under 齐全的时刻
            opening_gl = None
            for t in times:
                recs = [(s.goal_line, s.over_odds, s.under_odds, s.bookmaker) for s in snaps if s.snapshot_time == t]
                gl, _ = main_line_at(recs)
                if gl is not None:
                    opening_gl = gl
                    break
            # 收盘共识线: 最后一个时刻
            last_recs = [(s.goal_line, s.over_odds, s.under_odds, s.bookmaker) for s in snaps if s.snapshot_time == times[-1]]
            closing_gl, _ = main_line_at(last_recs)

            if opening_gl is not None:
                stats["can_open"] += 1
            if closing_gl is not None:
                stats["can_close"] += 1
            if opening_gl is not None and closing_gl is not None:
                stats["both"] += 1
                lg_have.setdefault(lg, 0)
                lg_have[lg] += 1
            else:
                lg_miss.setdefault(lg, 0)
                lg_miss[lg] += 1
                rows_miss.append((m.id, lg, len(snaps), len(times), opening_gl, closing_gl,
                                  times[0], times[-1], p.actual_total_goals))

        print(f"统计: {stats}")
        print(f"\n能同时算开盘+收盘线的联赛分布:")
        for lg, c in sorted(lg_have.items(), key=lambda x: -x[1]):
            print(f"  {lg:<8} {c}")
        print(f"\n缺失(无法算开盘或收盘)分布:")
        for lg, c in sorted(lg_miss.items(), key=lambda x: -x[1]):
            print(f"  {lg:<8} {c}")
        print(f"\n缺失明细 (id 联赛 行数 时刻数 开盘 收盘 首快照 末快照 实际):")
        for row in rows_miss[:20]:
            print(f"  {row[0]:<7}{row[1]:<8}rows={row[2]:<4}ts={row[3]:<3}open={row[4]} close={row[5]} "
                  f"{row[6]:%m-%d %H:%M}~{row[7]:%m-%d %H:%M} act={row[8]}")


asyncio.run(main())
