"""深挖：Excelsior/Heidenheim 正确 SM id + 35867/269225 占用 + 3 场 fixture 查找"""
import asyncio
import sys
sys.path.insert(0, ".")
from app.collector.sportmonks.client import SportMonksClient

SEARCHES = [
    "Excelsior",
    "Heidenheim",
    "Alverca",
]
# 需验证占用情况的 SM id
TEAM_IDS = [35867, 269225, 18263, 17798]


async def main():
    sm = SportMonksClient()
    print("========== SM 搜索 ==========")
    for q in SEARCHES:
        try:
            res = await sm.search_teams(q)
        except Exception as e:
            print(f"  search {q} 失败: {e}")
            continue
        print(f"  --- {q} ---")
        for t in res[:6]:
            print(f"      id={t.get('id')} name={t.get('name')}")

    print("\n========== 按 ID 查球队 ==========")
    for tid in TEAM_IDS:
        try:
            t = await sm.get_team_by_id(tid)
        except Exception as e:
            print(f"  id={tid} 查询失败: {e}")
            continue
        print(f"  id={tid}: {t.get('name')}")

    print("\n========== 查找 3 场未匹配比赛的 fixture ==========")
    # 08-15/16 比赛（UTC）: 08-15 09:00 J2(秋田vs富山), 08-15 11:00 德乙, 08-15 18:00 荷甲
    from datetime import datetime, timedelta
    # 用 fixtures/date 查询 08-15 和 08-16 UTC 日期
    import os
    for date in ["2026-08-15", "2026-08-16"]:
        try:
            fxs = await sm.get_fixtures_by_date(date, includes="participants")
        except Exception as e:
            print(f"  date={date} 查询失败: {e}")
            continue
        print(f"  --- {date} 共 {len(fxs)} 场 ---")
        for fx in fxs:
            parts = fx.get("participants", [])
            names = [(p.get("id"), p.get("name")) for p in parts if isinstance(p, dict)]
            n = ", ".join(f"{i}:{n}" for i, n in names)
            print(f"      fx={fx.get('id')} {fx.get('starting_at')} | {n}")

    await sm.close()


asyncio.run(main())
