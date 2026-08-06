"""直接用 J1 球队 ID 修正已有 fixture 的 league_id，无需重新拉API"""
import asyncio, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from app.db.database import async_session
from app.db.models import Match, Team, League
from app.collector.sportmonks.client import SportMonksClient
from sqlalchemy import select, update, func

J1_LOCAL = 7

async def main():
    c = SportMonksClient()
    try:
        # 1. 从 API 获取 J1 所有球队的 sportmonks_id
        print("获取日职联球队列表...")
        fixtures = await c.get_fixtures_by_date("2025-08-16", "participants;scores")
        j1_sm_ids = set()
        for f in fixtures:
            if f.get("league_id") == 968:
                for p in f.get("participants", []):
                    j1_sm_ids.add(p["id"])
        
        # 多采样几天确保覆盖所有球队
        for dt in ["2025-04-12","2025-07-20","2025-10-05"]:
            fx = await c.get_fixtures_by_date(dt, "participants;scores")
            for f in fx:
                if f.get("league_id") == 968:
                    for p in f.get("participants", []):
                        j1_sm_ids.add(p["id"])
        
        print(f"  找到 {len(j1_sm_ids)} 支日职联球队")
        
        # 2. 在数据库中找这些球队
        async with async_session() as db:
            teams = await db.execute(select(Team).where(Team.sportmonks_id.in_(j1_sm_ids)))
            local_team_ids = [t.id for t in teams.scalars().all()]
            print(f"  数据库匹配: {len(local_team_ids)} 支")
            
            # 3. 更新这些球队涉及的所有比赛的 league_id
            # 主队是日职联球队的比赛
            result1 = await db.execute(
                update(Match).where(Match.home_team_id.in_(local_team_ids)).values(league_id=J1_LOCAL)
            )
            # 客队是日职联球队的比赛
            result2 = await db.execute(
                update(Match).where(Match.away_team_id.in_(local_team_ids)).values(league_id=J1_LOCAL)
            )
            await db.commit()
            
            # 统计
            j1_total = await db.execute(select(func.count()).select_from(Match).where(Match.league_id==J1_LOCAL))
            j1_scored = await db.execute(select(func.count()).select_from(Match).where(
                Match.league_id==J1_LOCAL, Match.home_score.isnot(None)))
            
            print(f"\n日职联: 总计 {j1_total.scalar()} 场, 有比分 {j1_scored.scalar()} 场")
            
            # 更新联赛 sportmonks_id
            await db.execute(update(League).where(League.id==J1_LOCAL).values(sportmonks_id=968))
            await db.commit()
    finally:
        await c.close()

asyncio.run(main())
