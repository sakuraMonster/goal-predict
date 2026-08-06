"""直接调试 match_to_sportmonks"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta, date
from app.collector.pipeline import SyncPipeline
from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import Match
from sqlalchemy import select, func


async def main():
    # 1. 先手动查一下 API 返回
    sm = SportMonksClient()
    print("="*60)
    print("手动测试 API:")
    all_fixtures = []
    for d_str in ["2026-07-30", "2026-07-31", "2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"]:
        fx = await sm.get_fixtures_by_date(d_str, includes="participants")
        all_fixtures.extend(fx)
        print(f"  {d_str}: {len(fx)} fixtures")
    
    # 去重
    seen = set()
    deduped = []
    for fx in all_fixtures:
        if fx["id"] not in seen:
            seen.add(fx["id"])
            deduped.append(fx)
    print(f"  去重后: {len(deduped)} fixtures")

    # 2. 检查缺失比赛是否在 fixtures 中
    print("\n" + "="*60)
    print("检查缺失比赛:")
    
    async with async_session() as db:
        result = await db.execute(
            select(Match).where(
                Match.sportmonks_fixture_id.is_(None),
                func.date(Match.kickoff_time).in_([date(2026,8,1), date(2026,8,2)]),
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
            ).order_by(Match.kickoff_time)
        )
        unmatched = list(result.scalars().all())
        print(f"  无SM fixture比赛: {len(unmatched)} 场")
        
        for m in unmatched:
            home_sm = m.home_team.sportmonks_id if m.home_team else None
            away_sm = m.away_team.sportmonks_id if m.away_team else None
            home_name = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away_name = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
            
            found = None
            for fx in deduped:
                pids = set()
                for p in fx.get("participants", []):
                    if isinstance(p, dict):
                        pids.add(p.get("id"))
                if home_sm in pids and away_sm in pids:
                    found = fx
                    break
            
            if found:
                print(f"  ✓ ID={m.id} {home_name}({home_sm}) vs {away_name}({away_sm}) → fixture_id={found['id']} {found.get('name')}")
            else:
                # 尝试部分匹配
                for fx in deduped:
                    pids = set()
                    for p in fx.get("participants", []):
                        if isinstance(p, dict):
                            pids.add(p.get("id"))
                    if home_sm in pids:
                        found = fx
                        break
                if found:
                    fx_pids = [p.get("id") for p in found.get("participants", []) if isinstance(p, dict)]
                    print(f"  ✗ ID={m.id} {home_name}({home_sm}) vs {away_name}({away_sm}) → 只找到home在 fixture {found['id']} pids={fx_pids}")
                else:
                    print(f"  ✗ ID={m.id} {home_name}({home_sm}) vs {away_name}({away_sm}) → 双方都不在任何fixture中")
    
    await sm.close()
    
    # 3. 运行 match_to_sportmonks
    print("\n" + "="*60)
    print("运行 match_to_sportmonks:")
    pipeline = SyncPipeline()
    await pipeline.match_to_sportmonks()

asyncio.run(main())
