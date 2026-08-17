"""核查历史赛季数据完备性：2025赛季各联赛球队统计覆盖度
目的：判断能否基于 team_season_stats 提前预计算联赛基线
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func, and_
from app.db.database import async_session
from app.db.models import TeamSeasonStats, League, Team

OUT = os.path.join(os.path.dirname(__file__), "verify_season_coverage.txt")


async def main():
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        # 1) season 字段取值分布（全局）
        r = await db.execute(
            select(TeamSeasonStats.season, func.count(TeamSeasonStats.id))
            .group_by(TeamSeasonStats.season).order_by(func.count(TeamSeasonStats.id).desc())
        )
        p("=== season 字段取值分布（全部记录） ===")
        for season, cnt in r.all():
            p(f"  season={season!r}: {cnt} 条")

        # 2) 目标联赛：荷甲(17)/葡超(21)/以及对照组 挪超/芬超 的球队覆盖
        p("\n=== 各联赛 2025/2026 赛季球队统计覆盖度 ===")
        for lg_id, lg_name in [(17, "荷甲"), (21, "葡超"), (25, "挪超"), (27, "芬超")]:
            # 该联赛的球队
            r = await db.execute(
                select(func.count(Team.id)).where(Team.league_id == lg_id)
            )
            team_cnt = r.scalar_one()
            for season in ["2025", "2026"]:
                r = await db.execute(
                    select(
                        func.count(func.distinct(TeamSeasonStats.team_id)),
                        func.count(TeamSeasonStats.id),
                        func.avg(TeamSeasonStats.played),
                    ).where(TeamSeasonStats.season == season)
                )
                # 但 league_id 被污染=6，需通过 team 的 league_id 关联
                r = await db.execute(
                    select(
                        func.count(func.distinct(TeamSeasonStats.team_id)),
                        func.avg(TeamSeasonStats.played),
                    ).select_from(TeamSeasonStats)
                    .join(Team, Team.id == TeamSeasonStats.team_id)
                    .where(TeamSeasonStats.season == season, Team.league_id == lg_id)
                )
                cnt, avg_played = r.one()
                p(f"  {lg_name} id={lg_id} 球队数={team_cnt} season={season}: "
                  f"有统计球队={cnt} 场均played={avg_played and round(float(avg_played),1)}")

        # 3) 荷甲 2025 球队统计明细（看完整性 & recent_matches 是否有内容）
        p("\n=== 荷甲 2025 赛季球队统计明细 ===")
        r = await db.execute(
            select(TeamSeasonStats, Team.name_zh)
            .join(Team, Team.id == TeamSeasonStats.team_id)
            .where(TeamSeasonStats.season == "2025", Team.league_id == 17)
            .order_by(TeamSeasonStats.played.desc())
        )
        for stats, tname in r.all():
            rm = stats.recent_matches if isinstance(stats.recent_matches, list) else []
            p(f"  {tname}: played={stats.played} gf={stats.goals_for} ga={stats.goals_against} "
              f"w={stats.wins} d={stats.draws} l={stats.losses} recent={len(rm)}条 "
              f"league_id={stats.league_id}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出: {OUT}")


asyncio.run(main())
