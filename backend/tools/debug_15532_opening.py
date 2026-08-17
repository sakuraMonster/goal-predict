"""调试15532的维度B初盘共识线计算：逐时刻打印各博彩公司OU主盘线"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import OddsSnapshot
from collections import Counter

MATCH_ID = 15532


async def main():
    async with async_session() as db:
        result = await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id == MATCH_ID)
            .order_by(OddsSnapshot.snapshot_time.asc())
        )
        all_odds = list(result.scalars().all())
        print(f"总快照数: {len(all_odds)}")

        # 按时间分组
        by_time: dict = {}
        for o in all_odds:
            by_time.setdefault(o.snapshot_time, []).append(o)

        for t in sorted(by_time.keys()):
            recs = by_time[t]
            print(f"\n=== {t} (共{len(recs)}条) ===")
            # 每条记录: bookmaker, goal_line, over_odds, under_odds
            bm_lines = {}
            for o in recs:
                key = (o.bookmaker, o.goal_line)
                if o.over_odds is not None and o.under_odds is not None:
                    diff = abs(o.over_odds - o.under_odds)
                    bm_lines.setdefault(key, diff)
            # 每家公司主盘线
            bm_main = {}
            for (bm, gl), diff in bm_lines.items():
                if bm not in bm_main or diff < bm_main[bm][1]:
                    bm_main[bm] = (gl, diff)
            for bm, (gl, diff) in sorted(bm_main.items()):
                print(f"  {bm}: 主盘线={gl} diff={diff:.3f}")
            main_gls = [v[0] for v in bm_main.values()]
            if main_gls:
                c = Counter(main_gls)
                print(f"  → 众数: {c.most_common(1)[0][0] if c else 'N/A'} (分布{c.most_common(3)})")
            else:
                print(f"  → 无完整over+under记录")
            # 打印所有原始行
            for o in recs:
                print(f"    [{o.bookmaker}] GL={o.goal_line} over={o.over_odds} under={o.under_odds}")


asyncio.run(main())
