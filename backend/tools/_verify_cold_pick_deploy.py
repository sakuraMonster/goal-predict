"""验证冷门优选生产改动落地：
1. cold_pick_records 表存在
2. cold-picks 端点依赖的模型/字段可导入
3. scheduler sync_standings_08 任务注册
"""
import asyncio
from sqlalchemy import text

from app.db.database import engine, async_session
from app.db.models import ColdPickRecord, Match, OddsSnapshot, TeamSeasonStats


async def check_table_exists():
    async with engine.connect() as conn:
        r = await conn.execute(text(
            "SELECT to_regclass('public.cold_pick_records') AS tbl,"
            " (SELECT count(*) FROM information_schema.columns"
            "  WHERE table_name='cold_pick_records') AS ncols"
        ))
        row = r.fetchone()
        print(f"[表] cold_pick_records to_regclass={row.tbl} 列数={row.ncols}")
        return row.tbl is not None


async def check_standings_coverage():
    async with async_session() as db:
        r = await db.execute(text(
            "SELECT count(*) FILTER (WHERE league_position IS NOT NULL) AS ranked,"
            " count(*) AS total FROM team_season_stats"
        ))
        row = r.fetchone()
        print(f"[排名] team_season_stats 有联赛排名的球队数={row.ranked} / {row.total}")


async def check_scheduler():
    from app.scheduler import scheduler
    job_ids = [j.id for j in scheduler.get_jobs()]
    print(f"[调度] 已注册任务: {job_ids}")
    print(f"[调度] sync_standings_08 注册={'sync_standings_08' in job_ids}")


async def main():
    await check_table_exists()
    await check_standings_coverage()
    await check_scheduler()


if __name__ == "__main__":
    asyncio.run(main())
