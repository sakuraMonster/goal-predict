"""近30天 Model C snap_top2 命中率统计（读库，重预测后）
口径: 竞彩周期 2026-07-11 12:00 ~ 2026-08-11 12:00, 已结算比赛
输出: 全局 + 按联赛 snap_top2_c 命中率
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collections import defaultdict
from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match


async def main():
    start = datetime(2026, 7, 11, 12, 0, 0)
    end = datetime(2026, 8, 11, 12, 0, 0)
    async with async_session() as db:
        r = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        )
        preds = r.unique().scalars().all()

        by_lg = defaultdict(lambda: {"n": 0, "hit": 0, "lam": 0.0})
        detail = []
        for p in preds:
            m = p.match
            if not m or not m.league or p.actual_total_goals is None or p.snap_top2_c is None:
                continue
            lg = m.league.name_zh
            top2 = p.snap_top2_c
            hit = p.actual_total_goals in top2
            d = by_lg[lg]
            d["n"] += 1
            d["hit"] += hit
            d["lam"] += (p.expected_goals_c or 0)
            detail.append((m.id, lg, p.actual_total_goals, top2, hit))

        print(f"{'联赛':<8}{'场次':>5}{'命中':>5}{'命中率':>9}{'平均λ':>8}")
        print("-" * 40)
        total_n = total_hit = 0
        rows = sorted(by_lg.items(), key=lambda kv: -kv[1]["hit"] / max(kv[1]["n"], 1))
        for lg, d in rows:
            total_n += d["n"]
            total_hit += d["hit"]
            print(f"{lg:<8}{d['n']:>5}{d['hit']:>5}{d['hit']/d['n']*100:>8.1f}%{d['lam']/d['n']:>8.2f}")
        print("-" * 40)
        print(f"{'全局':<8}{total_n:>5}{total_hit:>5}{total_hit/total_n*100:>8.1f}%")

        print("\n未命中明细（前30条）:")
        miss = [x for x in detail if not x[4]]
        for mid, lg, act, top2, _ in sorted(miss, key=lambda x: x[0])[:30]:
            print(f"  {mid} {lg:<6} 实际{act}球 top2={top2}")


asyncio.run(main())
