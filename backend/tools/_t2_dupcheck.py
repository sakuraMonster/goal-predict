"""t2 合并方向确认：重复球队对在 matches 的引用分布 + 各条目基本信息
对: (新条目, 老条目)
  (1678, 402), (208, 424), (1646, 1683), (1662, 426), (1680, 430), (1651, 413)
"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Team, Match, TeamSeasonStats, League


async def main():
    async with async_session() as db:
        league_name = {}
        r = await db.execute(select(League.id, League.name_zh))
        for lg_id, nz in r.all():
            league_name[lg_id] = nz

        pairs = [(1678, 402), (208, 424), (1646, 1683), (1662, 426), (1680, 430), (1651, 413)]
        for new_id, old_id in pairs:
            print(f"\n===== 新条目 {new_id} vs 老条目 {old_id} =====")
            for tid in (new_id, old_id):
                t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one()
                # matches 引用（全部 + 2026-07后）
                r_all = await db.execute(select(func.count()).select_from(Match).where((Match.home_team_id == tid) | (Match.away_team_id == tid)))
                r_new = await db.execute(select(func.count()).select_from(Match).where(((Match.home_team_id == tid) | (Match.away_team_id == tid)) & (Match.kickoff_time >= datetime(2026, 7, 1, 0, 0))))
                r_ss = await db.execute(select(func.count()).select_from(TeamSeasonStats).where(TeamSeasonStats.team_id == tid))
                print(f"  id={tid} {t.name_zh}({t.name_en}) sm={t.sportmonks_id} league={t.league_id}({league_name.get(t.league_id,'?')}) "
                      f"matches全部={r_all.scalar()} 2026-07后={r_new.scalar()} stats={r_ss.scalar()}")

            # 2026-07 后新条目涉及的具体比赛
            r = await db.execute(select(Match.id, Match.home_team_id, Match.away_team_id, Match.league_id, Match.kickoff_time)
                                 .where((Match.home_team_id == new_id) | (Match.away_team_id == new_id),
                                        Match.kickoff_time >= datetime(2026, 7, 1, 0, 0)))
            for m in r.all():
                print(f"  新条目比赛: id={m.id} {m.home_team_id} vs {m.away_team_id} league={m.league_id} t={m.kickoff_time}")


asyncio.run(main())
