"""快速查看近期球队的 TeamSeasonStats 行结构与 recent_matches JSON 结构"""
import asyncio, os, sys, json
from datetime import datetime
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Prediction, TeamSeasonStats

async def main():
    async with async_session() as db:
        row = (await db.execute(
            select(Match.home_team_id, Match.kickoff_time)
            .join(Prediction, Prediction.match_id == Match.id)
            .where(Prediction.actual_home_score.isnot(None),
                   Prediction.kickoff_time >= datetime(2026, 8, 1, 12, 0))
            .limit(1)
        )).one()
        tid, ko = row
        print(f"match home_team_id={tid} kickoff={ko}")
        rows = (await db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid)
        )).all()
        for r in rows:
            s = r[0]
            print(f"season={s.season} played={s.played} w={s.wins} d={s.draws} l={s.losses} "
                  f"gf={s.goals_for} ga={s.goals_against} form={s.form} pos={s.league_position}")
            print(f"  recent_matches = {json.dumps(s.recent_matches, ensure_ascii=False)[:600]}")
            print("  ---")

asyncio.run(main())
