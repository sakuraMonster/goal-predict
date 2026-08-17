"""补充验证：
1) 荷甲/葡超比赛的 status/venue/match_num（判断是否杯赛/友谊赛混入）
2) _get_team_stats 到底选中哪个赛季记录
3) 这些联赛在 matches 表中的历史记录（为什么 baseline count=0）
"""
import asyncio
import json
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func, and_
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction, League, TeamSeasonStats

OUT = os.path.join(os.path.dirname(__file__), "verify_hj_ps.txt")


async def main():
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        # 1) 荷甲/葡超比赛记录详情
        for lg_name in ["荷甲", "葡超"]:
            result = await db.execute(
                select(Match).options(joinedload(Match.league))
                .where(Match.league_id.in_([17, 21]))
                .order_by(Match.kickoff_time)
            )
            ms = list(result.unique().scalars().all())
            p(f"\n=== {lg_name}: {len(ms)} 场比赛字段详情 ===")
            p(f"{'id':>6} {'status':<10} {'jc_id':<14} {'match_num':<12} {'kickoff':<16} 主vs客")
            for m in ms:
                if m.league.name_zh != lg_name:
                    continue
                p(f"{m.id:>6} {str(m.status):<10} {str(m.jc_match_id):<14} {str(m.match_num):<12} "
                  f"{m.kickoff_time.strftime('%m-%d %H:%M') if m.kickoff_time else '?':<16} "
                  f"{m.home_team_name} vs {m.away_team_name}")

        # 2) 荷甲/葡超在 matches 表的历史比赛总数（用于 baseline）
        for lg_id in [17, 21]:
            total = await db.execute(
                select(func.count(Match.id)).where(Match.league_id == lg_id)
            )
            finished = await db.execute(
                select(func.count(Match.id)).where(
                    Match.league_id == lg_id,
                    Match.status == "finished",
                    Match.home_score.isnot(None),
                )
            )
            any_score = await db.execute(
                select(func.count(Match.id)).where(
                    Match.league_id == lg_id,
                    Match.home_score.isnot(None),
                )
            )
            p(f"\n联赛 id={lg_id}: 总场次={total.scalar_one()}  "
              f"status=finished且比分={finished.scalar_one()}  任意有比分={any_score.scalar_one()}")

            # status 分布
            st = await db.execute(
                select(Match.status, func.count(Match.id)).where(Match.league_id == lg_id).group_by(Match.status)
            )
            p(f"  status 分布: {dict(st.all())}")

        # 3) 部分球队的 TeamSeasonStats 全部记录（看赛季混杂）
        p("\n\n=== 球队赛季记录详情（含 recent_matches 数量） ===")
        team_ids = {15539: [None], 15537: [None], 15541: [None], 15546: [None], 15556: [None], 15574: [None]}
        # 从比赛反查
        result = await db.execute(
            select(Match).where(Match.id.in_([15539, 15537, 15541, 15546, 15556, 15574]))
        )
        for m in result.scalars().all():
            for tid in [m.home_team_id, m.away_team_id]:
                if tid is None:
                    continue
                ts = await db.execute(
                    select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid).order_by(TeamSeasonStats.id.desc())
                )
                recs = ts.scalars().all()
                p(f"\nteam_id={tid} ({m.home_team_name if tid == m.home_team_id else m.away_team_name}):")
                for r in recs:
                    rm = r.recent_matches if isinstance(r.recent_matches, list) else []
                    p(f"  season={r.season!r} league_id={r.league_id} played={r.played} "
                      f"gf={r.goals_for} ga={r.goals_against} w={r.wins} d={r.draws} l={r.losses} "
                      f"recent={len(rm)}条")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出: {OUT}")


asyncio.run(main())
