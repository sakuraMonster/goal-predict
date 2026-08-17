"""复盘 08-11 竞彩周期：全部比赛 + 周一001 详情"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match


async def main():
    # 竞彩比赛日: 08-10 12:00 ~ 08-12 12:00（覆盖周一/周二两天）
    start = datetime(2026, 8, 10, 12, 0, 0)
    end = datetime(2026, 8, 12, 12, 0, 0)

    async with async_session() as db:
        r = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
            .order_by(Prediction.kickoff_time)
        )
        preds = r.unique().scalars().all()
        print(f"8-10~8-12 周期共 {len(preds)} 场已预测\n")
        print(f"{'id':<6}{'编号':<10}{'联赛':<8}{'主队':<12}{'客队':<12}{'开赛':>16}{'实':>3}{'λc':>6}{'top2c':>10}{'命中':>4}{'created':>20}")
        for p in preds:
            m = p.match
            if not m:
                continue
            act = p.actual_total_goals
            act_s = f"{act}" if act is not None else "-"
            hit = ""
            if act is not None and p.snap_top2_c:
                hit = "√" if min(act, 4) in p.snap_top2_c else "×"
            print(f"{m.id:<6}{str(m.match_num or ''):<10}{(m.league.name_zh if m.league else '?'):<8}"
                  f"{(m.home_team_name or '?'):<12}{(m.away_team_name or '?'):<12}"
                  f"{m.kickoff_time:%m-%d %H:%M}: {act_s:>3}"
                  f"{(p.expected_goals_c or 0):>6.2f}{str(p.snap_top2_c or []):>10}{hit:>4}"
                  f"{p.created_at:%m-%d %H:%M}:{p.created_at:%S}")


asyncio.run(main())
