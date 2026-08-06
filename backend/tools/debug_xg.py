"""调试：单球队验证 SportMonks xG 数据获取"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.db.database import engine
from app.db.models import Team
from app.collector.sportmonks.client import SportMonksClient

async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as db:
        # 找弗拉门戈（巴甲，大概率有 xG 数据）
        r = await db.execute(select(Team).where(Team.name_zh == "弗拉门戈"))
        team = r.scalar_one_or_none()
        if not team:
            print("未找到球队")
            return
        sm_id = team.sportmonks_id
        print(f"球队: {team.name_zh}, SM ID: {sm_id}")

    sm = SportMonksClient()

    # Step 1: get_team_by_id
    print("\n=== Step 1: get_team_by_id ===")
    team_data = await sm.get_team_by_id(sm_id, includes="statistics")
    stats_list = team_data.get("statistics", [])
    print(f"statistics 条目数: {len(stats_list)}")
    for s in stats_list[:3]:
        print(f"  season_id={s.get('season_id')} has_values={s.get('has_values')}")

    # 找活跃赛季
    active = [s for s in stats_list if isinstance(s, dict) and s.get("has_values") and s.get("season_id")]
    candidates = active if active else [s for s in stats_list if isinstance(s, dict) and s.get("season_id")]
    if not candidates:
        print("无可用赛季!")
        await sm.close()
        return
    best = max(candidates, key=lambda s: s["season_id"])
    season_id = best["season_id"]
    print(f"尝试 season_id={season_id}")

    # Step 2: get_statistics_by_season_team
    print(f"\n=== Step 2: get_statistics_by_season_team({season_id}, {sm_id}) ===")
    try:
        season_stats = await sm.get_statistics_by_season_team(season_id, sm_id)
    except Exception as e:
        print(f"  404! 回退到 get_statistics_by_season_team(0, {sm_id})")
        # 尝试 season_id=0（返回所有赛季列表）
        season_stats = await sm.get_statistics_by_season_team(0, sm_id)
    print(f"返回条目数: {len(season_stats) if isinstance(season_stats, list) else 'N/A'}")
    
    if isinstance(season_stats, list) and season_stats:
        first = season_stats[0]
        print(f"第一条 keys: {list(first.keys()) if isinstance(first, dict) else 'not dict'}")
        
        details = first.get("details", []) if isinstance(first, dict) else []
        print(f"details 条目数: {len(details)}")
        
        # 找 xG 相关的 type_id
        for d in details[:5]:
            print(f"  type_id={d.get('type_id')} value={d.get('value')}")
        
        # 查找所有 type_id
        all_types = {}
        for d in details:
            tid = d.get("type_id")
            if tid:
                all_types[tid] = d.get("value")
        print(f"\n所有 type_id ({len(all_types)}个):")
        for tid, val in sorted(all_types.items()):
            print(f"  {tid}: {val}")

        # 尝试找 xG (type_id 可能在更多行中)
        for i, ss in enumerate(season_stats):
            if not isinstance(ss, dict): continue
            dets = ss.get("details", [])
            for d in dets:
                tid = d.get("type_id")
                if tid in [347, 201, 5304, 348, 202]:
                    print(f"  找到! season[{i}] type_id={tid} value={d.get('value')}")

    await sm.close()

asyncio.run(main())
