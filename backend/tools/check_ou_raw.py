"""检查日职/德乙比赛最新快照的原始 OU 行：over/under 配对与水位"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import OddsSnapshot, Match

MATCH_IDS = [15547, 15548, 15551, 15552, 15529]


async def main():
    async with async_session() as db:
        for mid in MATCH_IDS:
            mq = await db.execute(select(Match).where(Match.id == mid))
            m = mq.scalar_one_or_none()
            rs = await db.execute(
                select(OddsSnapshot).where(OddsSnapshot.match_id == mid)
                .order_by(OddsSnapshot.snapshot_time.asc())
            )
            all_odds = list(rs.scalars().all())
            by_time = {}
            for o in all_odds:
                by_time.setdefault(o.snapshot_time, []).append(o)
            times = sorted(by_time.keys())
            print(f"\n{'='*70}\n{mid} {m.home_team_name} vs {m.away_team_name} 快照时点{len(times)}个")

            # 打印最后2个时点的所有 OU 行
            for t in times[-2:]:
                print(f"\n--- {t} (UTC) ---")
                rows = by_time[t]
                # 只打印 OU 字段非空的行
                for o in rows:
                    gl = round(float(o.goal_line), 2) if o.goal_line is not None else None
                    ov = o.over_odds
                    un = o.under_odds
                    if ov is None and un is None:
                        continue
                    diff = f"{abs(ov-un):.2f}" if ov is not None and un is not None else "-"
                    print(f"  [{o.bookmaker}] GL={gl} over={ov} under={un} diff={diff}")


asyncio.run(main())
