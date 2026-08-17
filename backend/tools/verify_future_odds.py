"""验证 sync_odds 后未来比赛的快照状态：时间点数、多线OU、双赔率情况"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Match, League, OddsSnapshot


async def main():
    now = datetime.now()
    future_start = now.replace(hour=12, minute=0, second=0, microsecond=0)
    future_end = now + timedelta(days=7)

    async with async_session() as db:
        rl = await db.execute(select(League.id, League.name_zh))
        lmap = {r[0]: r[1] for r in rl}

        rq = await db.execute(
            select(Match).where(
                Match.kickoff_time >= future_start,
                Match.kickoff_time <= future_end,
                Match.status == "scheduled",
            ).order_by(Match.kickoff_time)
        )
        matches = list(rq.scalars().all())
        print(f"未来比赛（{future_start:%m-%d %H:%M} ~ {future_end:%m-%d %H:%M}）: {len(matches)} 场\n")

        dual_total, multi_total = 0, 0
        for m in matches:
            lg = lmap.get(m.league_id, "?")
            rs = await db.execute(
                select(
                    func.count(OddsSnapshot.id),
                    func.count(func.distinct(OddsSnapshot.snapshot_time)),
                    func.max(OddsSnapshot.snapshot_time),
                ).where(OddsSnapshot.match_id == m.id)
            )
            cnt, n_times, last_t = rs.one()
            # OU 双赔率行数（over+under 齐全）
            rdual = await db.execute(
                select(func.count(OddsSnapshot.id)).where(
                    OddsSnapshot.match_id == m.id,
                    OddsSnapshot.over_odds.isnot(None),
                    OddsSnapshot.under_odds.isnot(None),
                )
            )
            dual = rdual.scalar() or 0
            dual_total += dual
            multi_total += 1 if dual > 0 else 0
            kt = m.kickoff_time.strftime("%m-%d %H:%M") if m.kickoff_time else "?"
            last_s = last_t.strftime("%m-%d %H:%M") if last_t else "无"
            print(f"  {m.id} [{lg}] {m.home_team_name} vs {m.away_team_name} "
                  f"| 开赛{kt} | 快照{cnt}条/{n_times}时点 | 最新{last_s}(UTC)"
                  f" | OU双赔率{dual}行")

        print(f"\nOU双赔率行总计: {dual_total}（{multi_total}/{len(matches)} 场有双赔率数据）")


asyncio.run(main())
