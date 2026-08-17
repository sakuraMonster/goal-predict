"""t1 最终校验：
1. 回滚 新潟天鹅(1638) -> None（基于脏比赛 15530 的不可信更新）
2. 验证 圣何塞(271)/坦佩雷山猫(282) 单票更新的比赛来源是否可信
"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Team, Match, League


async def main():
    async with async_session() as db:
        league_name = {}
        r = await db.execute(select(League.id, League.name_zh))
        for lg_id, nz in r.all():
            league_name[lg_id] = nz

        # 1. 回滚新潟天鹅
        t = (await db.execute(select(Team).where(Team.id == 1638))).scalar_one()
        print(f"新潟天鹅(1638) 当前={t.league_id}({league_name.get(t.league_id,'?')}) -> 回滚 None")
        t.league_id = None
        await db.commit()
        print("  已提交")

        # 2. 验证单票更新的比赛来源
        for tid in [271, 282]:
            t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one()
            print(f"\n== {t.name_zh}({tid}) 当前={t.league_id}({league_name.get(t.league_id,'?')}) ==")
            r = await db.execute(select(Match).where(
                (Match.home_team_id == tid) | (Match.away_team_id == tid),
                Match.kickoff_time >= datetime(2026, 6, 1, 0, 0)).order_by(Match.kickoff_time.desc()))
            for m in r.scalars().all():
                opp_id = m.away_team_id if m.home_team_id == tid else m.home_team_id
                opp = (await db.execute(select(Team.name_zh).where(Team.id == opp_id))).scalar_one_or_none()
                ha = "主" if m.home_team_id == tid else "客"
                print(f"  id={m.id} {ha} 对手={opp}({opp_id}) league={m.league_id}({league_name.get(m.league_id,'?')}) t={m.kickoff_time} jc={m.jc_match_id}")


asyncio.run(main())
