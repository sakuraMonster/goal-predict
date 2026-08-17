"""验证 15578 主盘线识别噪声: 对比 08-10 12:00 与 15:00 两个快照批次
每家公司各行(goal_line, over, under, |ov-un|) → 主盘线(min diff) → 众数
"""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import OddsSnapshot

MID = 15578

TIMES = [
    datetime(2026, 8, 9, 5, 9),
    datetime(2026, 8, 10, 12, 0),
    datetime(2026, 8, 10, 15, 0),
    datetime(2026, 8, 10, 22, 0),
    datetime(2026, 8, 10, 23, 59),
]


async def main():
    async with async_session() as db:
        r = await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id == MID).order_by(OddsSnapshot.snapshot_time.asc())
        )
        all_odds = list(r.scalars().all())

        for t in TIMES:
            # 找到该时刻的批次(可能实际快照时间是 12:01 之类, 取 <=t 的最后一个时间)
            available = sorted(set(o.snapshot_time for o in all_odds if o.snapshot_time <= t))
            actual_t = available[-1] if available else None
            if actual_t is None:
                print(f"!! {t} 无快照")
                continue
            batch = [o for o in all_odds if o.snapshot_time == actual_t]
            # 过滤 OU 范围 1.5~3.5 (universal) — 与 _compute_ou_features 一致
            rows = []
            for o in batch:
                if o.goal_line is None or (o.over_odds is None and o.under_odds is None):
                    continue
                gl = round(float(o.goal_line), 2)
                if 1.5 <= gl <= 3.5:
                    rows.append((o.bookmaker, gl, o.over_odds, o.under_odds))
            # 每家公司主盘线
            bm_main = {}
            for bm, gl, ov, un in rows:
                if ov is None or un is None:
                    continue
                diff = abs(ov - un)
                if bm not in bm_main or diff < bm_main[bm][1]:
                    bm_main[bm] = (gl, diff)
            main_gls = [v[0] for v in bm_main.values()]
            cnt = Counter(main_gls)
            print(f"\n=== 快照批次 {actual_t:%m-%d %H:%M} (行数={len(rows)}, 公司数={len(bm_main)}) ===")
            print(f"  主盘线分布: {dict(cnt)}  众数={cnt.most_common(1)[0] if cnt else None}")
            # 展示若干公司明细
            shown = 0
            for bm, gl, ov, un in sorted(rows, key=lambda x: (x[0], x[1])):
                if shown >= 8:
                    break
                mark = "←主盘线" if (bm, gl) in [(b, g) for b, (g, _) in bm_main.items()] and bm_main[bm][0] == gl else ""
                print(f"    {str(bm)[:16]:<18} GL={gl:.2f} over={ov or 0:.2f} under={un or 0:.2f} |diff|={abs((ov or 0)-(un or 0)):.2f} {mark}")
                shown += 1


asyncio.run(main())
