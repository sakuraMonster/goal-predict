"""t2 诊断：波尔图等葡超球队 TeamSeasonStats 现状 + sportmonks 映射核查
1. 波尔图(1299) / Porto(486) / 阿尔维卡(1678) 等 TeamSeasonStats 全部记录
2. sm=1498 / sm=652 对应的 Team
3. 葡超重点球队 sm_id 与其 TeamSeasonStats.league_id 分布
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Team, TeamSeasonStats, League


async def main():
    async with async_session() as db:
        league_name = {}
        r = await db.execute(select(League.id, League.name_zh))
        for lg_id, nz in r.all():
            league_name[lg_id] = nz

        # 1. 波尔图相关 Team 记录
        print("== Team 表 sm 映射核查 ==")
        r = await db.execute(select(Team).where(Team.sportmonks_id.in_([1498, 652, 1822])))
        for t in r.scalars().all():
            print(f"  id={t.id} sm={t.sportmonks_id} {t.name_zh} / {t.name_en} league={t.league_id}({league_name.get(t.league_id,'?')}) needs_review={t.needs_review}")

        # 2. 葡超重点球队 TeamSeasonStats
        focus = [1299, 486, 1678, 1646, 208, 1662, 1680, 1651, 785, 388, 389, 401, 408, 422]
        print("\n== 葡超重点球队 TeamSeasonStats ==")
        for tid in focus:
            t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one_or_none()
            if not t:
                print(f"  id={tid}: Team 不存在")
                continue
            r = await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid).order_by(TeamSeasonStats.season.desc()))
            stats = r.scalars().all()
            if not stats:
                print(f"  id={tid} {t.name_zh}(sm={t.sportmonks_id}): 无 TeamSeasonStats")
                continue
            for s in stats:
                rm = s.recent_matches
                rm_info = f"rm={len(rm)}条" if isinstance(rm, list) else f"rm={type(rm).__name__}"
                first_opp = ""
                if isinstance(rm, list) and rm:
                    first_opp = f" 首场对手={rm[0].get('opponent_sm_id')}"
                print(f"  id={tid} {t.name_zh}(sm={t.sportmonks_id}): season={s.season} league={s.league_id}({league_name.get(s.league_id,'?')}) "
                      f"P={s.played} W={s.wins} D={s.draws} L={s.losses} GF={s.goals_for} GA={s.goals_against} {rm_info}{first_opp}")


asyncio.run(main())
