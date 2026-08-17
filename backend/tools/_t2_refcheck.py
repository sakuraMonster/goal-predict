"""t2 修复前检查：波尔图 486/1299 在其他表引用情况 + 需修正 sm_id 的球队
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func, text
from app.db.database import async_session
from app.db.models import Team


async def main():
    async with async_session() as db:
        # 检查 486 / 1299 在各表的引用
        for tid in [486, 1299]:
            t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one()
            print(f"\n== {t.name_zh}({tid}) sm={t.sportmonks_id} ==")
            for table, col in [
                ("matches_home", "home_team_id"), ("matches_away", "away_team_id"),
                ("team_season_stats", "team_id"), ("team_aliases", "team_id"),
            ]:
                try:
                    r = await db.execute(text(f"SELECT COUNT(*) FROM {table.split('_')[0] if table!='matches_home' and table!='matches_away' else 'matches'} WHERE {col} = {tid}"))
                    print(f"  {table}.{col}: {r.scalar()}")
                except Exception as e:
                    print(f"  {table}: 错误 {e}")

        # head_to_head / predictions / odds_snapshots 引用
        for tid in [486, 1299]:
            for table, col in [
                ("head_to_head", "home_team_id"), ("head_to_head", "away_team_id"),
                ("predictions", "home_team_id"), ("predictions", "away_team_id"),
            ]:
                try:
                    r = await db.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {col} = {tid}"))
                    print(f"  {table}.{col}: {r.scalar()}")
                except Exception as e:
                    pass


asyncio.run(main())
