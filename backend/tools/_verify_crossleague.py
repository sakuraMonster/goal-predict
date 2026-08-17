"""验证串台过滤失效原因 + Team 表反查
检查：
1. 各队 Team.league_id（反查真实联赛）
2. 串台对手（Folkestone/Minnesota/Auxerre/Hitchin Town/Bragantino/Dinamo Minsk/AVS）
   在 Team 表中的存在性和 league_id
3. 各队 2025/2026 赛季记录是否存在
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Team, TeamSeasonStats, League


async def main():
    async with async_session() as db:
        async def q(team_ids):
            out = {}
            for tid in team_ids:
                r = await db.execute(select(Team).where(Team.id == tid))
                t = r.scalar_one_or_none()
                if t:
                    out[tid] = (t.name_zh, t.sportmonks_id, t.league_id)
                else:
                    out[tid] = None
            return out

        teams = await q([388, 1651, 1646, 208, 1662, 389, 1299, 1678, 1680, 401, 408, 422, 785])
        print("== 本队 Team.league_id 反查 ==")
        for tid, v in teams.items():
            print(f"  team_id={tid}: {v}")

        # 对手 sm_id → league_id
        opp_sm_ids = [1347, 147671, 3639, 1329, 7808, 3791, 269225, 4092, 3682, 12152]
        print("\n== 串台对手 sportmonks_id → Team.league_id ==")
        for sm in opp_sm_ids:
            r = await db.execute(select(Team).where(Team.sportmonks_id == sm))
            t = r.scalar_one_or_none()
            if t:
                print(f"  sm_id={sm}: {t.name_zh} league_id={t.league_id}")
            else:
                print(f"  sm_id={sm}: 不在 Team 表")

        # 各队 2025/2026 赛季记录
        print("\n== 各队 2025/2026 赛季记录存在性 ==")
        for tid in [1299, 1678, 1646, 208, 1662, 1680, 1651]:
            r = await db.execute(
                select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid)
            )
            recs = r.scalars().all()
            seasons = [(s.season, s.played, s.goals_for, s.goals_against, 
                        len(s.recent_matches) if isinstance(s.recent_matches, list) else 0)
                       for s in recs]
            print(f"  team_id={tid}: {seasons}")

        # 葡超 league_id
        print("\n== 葡超 league_id ==")
        r = await db.execute(select(League).where(League.name_zh == "葡超"))
        for lg in r.scalars().all():
            print(f"  id={lg.id} name_zh={lg.name_zh} name_en={lg.name_en} sm={lg.sportmonks_id}")

        # Team 表 league_id 分布 & 污染范围
        print("\n== Team 表 league_id 分布（前15）==")
        r = await db.execute(
            select(Team.league_id, func.count(Team.id).label("cnt"))
            .group_by(Team.league_id).order_by(func.count(Team.id).desc())
        )
        for lg_id, cnt in r.all():
            lname = "?"
            if lg_id is not None:
                rr = await db.execute(select(League).where(League.id == lg_id))
                l = rr.scalar_one_or_none()
                lname = l.name_zh if l else "?"
            print(f"  league_id={lg_id} ({lname}): {cnt} 队")

        # league_id=6 的队伍样本（应都是葡超？）
        print("\n== league_id=6 的队伍样本 ==")
        r = await db.execute(
            select(Team).where(Team.league_id == 6).order_by(Team.id).limit(20)
        )
        for t in r.scalars().all():
            print(f"  id={t.id} sm={t.sportmonks_id} {t.name_zh}")


asyncio.run(main())
