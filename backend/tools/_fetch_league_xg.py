"""回填目标联赛的逐场 xG（SportMonks xGFixture），缓存到 /tmp/xg_cache.json。

只覆盖有 xG 的联赛（韩K 源无 xG，已排除）：
  日职联 / 西甲 / 英超 / 德甲 / 意甲 / 法甲

缓存结构：{fixture_id: {participant_id(球队 smid): xG}}，逐队拉取后按 fixture 合并去重。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_fetch_league_xg.py
"""
import os, sys, json, asyncio
import asyncpg

sys.path.insert(0, "/app")
from app.collector.sportmonks.client import SportMonksClient

LEAGUES = ["日职联", "西甲", "英超", "德甲", "意甲", "法甲"]
DATE_FROM = "2025-01-01"
DATE_TO = "2026-10-22"
OUT = "/tmp/xg_cache.json"


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(
        "SELECT DISTINCT t.sportmonks_id AS smid, l.name_zh AS lg "
        "FROM teams t JOIN leagues l ON l.id = t.league_id "
        "WHERE l.name_zh = ANY($1::text[]) AND t.sportmonks_id IS NOT NULL",
        LEAGUES,
    )
    await conn.close()

    teams = [(r["smid"], r["lg"]) for r in rows]
    print(f"目标球队数: {len(teams)}  (联赛: {LEAGUES})")

    sm = SportMonksClient()
    sm._min_interval = 0.4  # 提速（套餐 180/min）

    cache = {}          # fixture_id -> {participant_id: xG}
    fixtures_seen = 0
    for i, (smid, lg) in enumerate(teams, 1):
        try:
            fx_list = await sm.get_fixtures_between_for_team(
                DATE_FROM, DATE_TO, smid, includes="xGFixture.type"
            )
        except Exception as e:
            print(f"  [{i}/{len(teams)}] team {smid}({lg}) 拉取失败: {e}")
            continue
        if not isinstance(fx_list, list):
            continue
        for fx in fx_list:
            if not isinstance(fx, dict):
                continue
            fid = fx.get("id")
            xgfx = fx.get("xgfixture", [])
            if not fid or not isinstance(xgfx, list):
                continue
            for x in xgfx:
                if not isinstance(x, dict) or x.get("type_id") != 5304:
                    continue
                pid = x.get("participant_id")
                if pid is None or not isinstance(x.get("data"), dict):
                    continue
                try:
                    val = float(x["data"].get("value"))
                except (TypeError, ValueError):
                    continue
                cache.setdefault(fid, {})[pid] = val
        if i % 10 == 0 or i == len(teams):
            print(f"  [{i}/{len(teams)}] 已缓存 fixture 数: {len(cache)}")

    await sm.close()

    with open(OUT, "w") as f:
        json.dump(cache, f)
    print(f"完成。缓存 fixture 数: {len(cache)} -> {OUT}")


asyncio.run(main())
