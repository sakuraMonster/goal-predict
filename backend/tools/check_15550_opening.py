"""检查15550（shift=0.5）初盘各时刻数据，确认维度B修复有效"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import OddsSnapshot
from collections import Counter

MATCH_ID = 15550


async def main():
    async with async_session() as db:
        result = await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id == MATCH_ID)
            .order_by(OddsSnapshot.snapshot_time.asc())
        )
        all_odds = list(result.scalars().all())
        print(f"总快照数: {len(all_odds)}")

        by_time: dict = {}
        for o in all_odds:
            by_time.setdefault(o.snapshot_time, []).append(o)

        # 只打印有 OU 数据的时刻
        for t in sorted(by_time.keys()):
            recs = by_time[t]
            ou_recs = [o for o in recs if o.goal_line is not None and (o.over_odds is not None or o.under_odds is not None)]
            if not ou_recs:
                continue
            # 去重统计
            bm_lines = {}
            for o in ou_recs:
                key = (o.bookmaker, round(float(o.goal_line), 2))
                if o.over_odds is not None and o.under_odds is not None:
                    diff = abs(o.over_odds - o.under_odds)
                    if key not in bm_lines or diff < bm_lines[key][0]:
                        bm_lines[key] = (diff, o.over_odds, o.under_odds)
            print(f"\n=== {t} ===")
            bm_main = {}
            for (bm, gl), (diff, ov, un) in bm_lines.items():
                if bm not in bm_main or diff < bm_main[bm][1]:
                    bm_main[bm] = (gl, diff)
            for bm, (gl, diff) in sorted(bm_main.items()):
                print(f"  {bm}: 主盘线={gl} diff={diff:.3f}")
            main_gls = [v[0] for v in bm_main.values()]
            if main_gls:
                c = Counter(main_gls)
                print(f"  → 众数: {c.most_common(1)[0][0]} (分布{c.most_common(4)})")
            # 打印原始行（去重）
            seen = set()
            for o in ou_recs:
                key = (o.bookmaker, round(float(o.goal_line), 2))
                if key in seen:
                    continue
                seen.add(key)
                print(f"    [{o.bookmaker}] GL={o.goal_line} over={o.over_odds} under={o.under_odds}")


asyncio.run(main())
