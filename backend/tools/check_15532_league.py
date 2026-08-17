"""检查15532的match记录与league关联，并查找近期有完整初盘数据（含单边前夜快照）的比赛验证维度B"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, OddsSnapshot, League
from collections import Counter
from datetime import datetime, timedelta

MATCH_ID = 15532


async def check_15532():
    async with async_session() as db:
        r = await db.execute(
            select(Match).options(joinedload(Match.league)).where(Match.id == MATCH_ID)
        )
        m = r.unique().scalar_one_or_none()
        if m:
            print(f"15532: jc={m.jc_match_id} 主={m.home_team_name} 客={m.away_team_name}")
            print(f"  league_id={m.league_id} league_obj={m.league}")
            print(f"  venue(联赛名)={m.venue}")
            print(f"  kickoff={m.kickoff_time}")
        else:
            print("15532 match 不存在")

        # 近期比赛：有前夜(>=12h前)快照 + 当前完整数据
        now = datetime.now()
        cutoff = now - timedelta(hours=72)
        r2 = await db.execute(
            select(OddsSnapshot.match_id, func.min(OddsSnapshot.snapshot_time), func.count(OddsSnapshot.id))
            .where(OddsSnapshot.snapshot_time >= cutoff)
            .group_by(OddsSnapshot.match_id)
            .order_by(func.min(OddsSnapshot.snapshot_time).asc())
        )
        rows = r2.all()
        print(f"\n近72h有快照的比赛数: {len(rows)}")
        # 找首条快照时间最早的几场
        for mid, first_t, cnt in rows[:15]:
            r3 = await db.execute(
                select(Match).options(joinedload(Match.league)).where(Match.id == mid)
            )
            m3 = r3.unique().scalar_one_or_none()
            ln = m3.league.name_zh if m3 and m3.league else "?"
            print(f"  {mid}: {ln} 首快照={first_t} 条数={cnt} 主={m3.home_team_name if m3 else '?'} 客={m3.away_team_name if m3 else '?'}")


asyncio.run(check_15532())
