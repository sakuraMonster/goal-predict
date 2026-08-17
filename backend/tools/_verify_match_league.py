"""验证 matches 表 league_id 真实性：
1. League id=6/12/13/21 分别是什么
2. 各 league_id 的 finished matches 数量分布
3. 吉马良斯(388)/里斯本竞技(389)/波尔图(1299) 在 matches 表的 league_id 分布
4. 15540/15556 等葡超比赛自身的 league_id
"""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import League, Match


async def main():
    async with async_session() as db:
        print("== League 表关键 id ==")
        r = await db.execute(select(League.id, League.name_zh, League.name_en).where(League.id.in_([6, 12, 13, 21, 22, 17])))
        for lg_id, nz, ne in r.all():
            print(f"  id={lg_id}: {nz} / {ne}")

        print("\n== matches 表 league_id 数量分布（前12）==")
        r = await db.execute(
            select(Match.league_id, func.count(Match.id))
            .group_by(Match.league_id).order_by(func.count(Match.id).desc())
        )
        for lg_id, cnt in r.all()[:12]:
            lname = "?"
            if lg_id is not None:
                rr = await db.execute(select(League.name_zh).where(League.id == lg_id))
                lname = rr.scalar_one_or_none() or "?"
            print(f"  league_id={lg_id} ({lname}): {cnt}")

        print("\n== 葡超(21) vs 韩K(6) finished 数量 ==")
        for lg_id in [6, 21]:
            r = await db.execute(
                select(func.count(Match.id)).where(Match.league_id == lg_id, Match.status == "finished")
            )
            print(f"  league_id={lg_id}: finished={r.scalar()}")

        print("\n== 重点球队 matches 表 league_id 分布 ==")
        for tid, name in [(388, "吉马良斯"), (389, "里斯本竞技"), (1299, "波尔图"), (401, "Moreirense"), (208, "卡萨皮亚")]:
            cnt = Counter()
            r = await db.execute(select(Match.league_id).where(
                (Match.home_team_id == tid) | (Match.away_team_id == tid)
            ))
            for (lg_id,) in r.all():
                cnt[lg_id] += 1
            print(f"  {name}({tid}): {dict(cnt)}")

        print("\n== 葡超竞彩比赛样本的 league_id ==")
        r = await db.execute(
            select(Match.id, Match.home_team_id, Match.away_team_id, Match.league_id, Match.kickoff_time)
            .where(Match.league_id == 21).order_by(Match.kickoff_time.desc()).limit(10)
        )
        for m in r.all():
            print(f"  id={m.id} home={m.home_team_id} away={m.away_team_id} league_id={m.league_id} t={m.kickoff_time}")

        print("\n== 15540/15556 的 league_id ==")
        r = await db.execute(select(Match.id, Match.league_id, Match.kickoff_time).where(Match.id.in_([15540, 15556, 15546, 15538])))
        for m in r.all():
            print(f"  id={m.id} league_id={m.league_id} t={m.kickoff_time}")


asyncio.run(main())
