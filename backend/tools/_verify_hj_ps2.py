"""最终验证：
1) league_id=6 是哪个联赛（为何荷甲/葡超球队赛季统计都挂 league_id=6）
2) 15541 PSV 线上 snap_top2_c=[3,2] 之谜
3) 荷甲/葡超球队统计记录的 created_at（新旧）
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import League, Match, Prediction, TeamSeasonStats, OddsSnapshot

OUT = os.path.join(os.path.dirname(__file__), "verify_hj_ps2.txt")


async def main():
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        # 1) league_id=6 是什么
        for lg_id in [5, 6, 17, 21]:
            r = await db.execute(select(League).where(League.id == lg_id))
            lg = r.scalar_one_or_none()
            p(f"league id={lg_id}: {lg.name_zh if lg else '无'} / {lg.name_en if lg else ''} sm={lg.sportmonks_id if lg else ''}")

        # 2) 15541 PSV 预测记录
        p("\n=== 15541 PSV vs 福图纳 预测记录 ===")
        r = await db.execute(select(Prediction).where(Prediction.match_id == 15541))
        pred = r.scalar_one_or_none()
        if pred:
            p(f"  expected_goals_c={pred.expected_goals_c}")
            p(f"  snap_top2_c={pred.snap_top2_c}")
            p(f"  expected_goals={pred.expected_goals}")
            p(f"  snap_top2={pred.snap_top2}")
            p(f"  created_at={pred.created_at}")
            p(f"  confidence={pred.confidence_level}")
            # detail 字段
            p(f"  summary_text={str(pred.summary_text)[:300] if pred.summary_text else None}")

        # 3) PSV 2026 赛季记录 created_at
        p("\n=== team_id=470 (PSV) 全部赛季记录 ===")
        r = await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id == 470).order_by(TeamSeasonStats.id))
        for s in r.scalars().all():
            rm = s.recent_matches if isinstance(s.recent_matches, list) else []
            first_rm = rm[0] if rm else None
            p(f"  season={s.season!r} league_id={s.league_id} played={s.played} gf={s.goals_for} ga={s.goals_against} "
              f"w={s.wins} d={s.draws} l={s.losses} recent={len(rm)} 首条={str(first_rm)[:120]}")

        # 4) 波尔图 1299 recent_matches 内容
        p("\n=== team_id=1299 (波尔图) season=latest recent_matches ===")
        r = await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id == 1299))
        for s in r.scalars().all():
            rm = s.recent_matches if isinstance(s.recent_matches, list) else []
            p(f"  season={s.season!r} league_id={s.league_id} played={s.played} gf={s.goals_for} ga={s.goals_against}")
            for m in rm[:10]:
                p(f"    {m}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出: {OUT}")


asyncio.run(main())
