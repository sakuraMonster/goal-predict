"""直接HTTP调试SM API - 看原始响应结构和league信息"""
import asyncio
import httpx
import os
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("SPORTMONKS_API_KEY", "")
BASE = "https://api.sportmonks.com/v3/football"


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        # 测试1: 单日 fixtures - 带 league 信息
        print("=" * 60)
        print("测试1: fixtures/date/2026-08-02 + include=league;participants")
        params = {"api_token": API_KEY, "include": "league;participants", "per_page": 100}
        resp = await client.get("/fixtures/date/2026-08-02", params=params)
        data = resp.json()
        fixtures = data.get("data", [])
        pagination = data.get("pagination", {})
        print(f"  返回: {len(fixtures)} fixtures, has_more={pagination.get('has_more')}, total={pagination.get('count')}")

        # 统计league
        leagues = {}
        for fx in fixtures:
            lg = fx.get("league", {})
            lg_id = lg.get("id", "?") if isinstance(lg, dict) else fx.get("league_id", "?")
            lg_name = lg.get("name", "?") if isinstance(lg, dict) else "?"
            key = f"{lg_name}(id={lg_id})"
            leagues[key] = leagues.get(key, 0) + 1

        for lg, cnt in sorted(leagues.items(), key=lambda x: -x[1]):
            print(f"    {lg}: {cnt}")

        # 测试2: fixtures/between - 使用更大的范围
        print("\n" + "=" * 60)
        print("测试2: fixtures/between/2026-07-28/2026-08-05 + league;participants, per_page=200")
        params2 = {"api_token": API_KEY, "include": "league;participants", "per_page": 200}
        resp2 = await client.get("/fixtures/between/2026-07-28/2026-08-05", params=params2)
        data2 = resp2.json()
        fixtures2 = data2.get("data", [])
        pagination2 = data2.get("pagination", {})
        print(f"  返回: {len(fixtures2)} fixtures, has_more={pagination2.get('has_more')}, total={pagination2.get('count')}, per_page={pagination2.get('per_page')}, current_page={pagination2.get('current_page')}")

        if fixtures2:
            # 第一页league分布
            leagues2 = {}
            for fx in fixtures2:
                lg = fx.get("league", {})
                lg_id = lg.get("id", "?") if isinstance(lg, dict) else fx.get("league_id", "?")
                lg_name = lg.get("name", "?") if isinstance(lg, dict) else "?"
                key = f"{lg_name}(id={lg_id})"
                leagues2[key] = leagues2.get(key, 0) + 1

            for lg, cnt in sorted(leagues2.items(), key=lambda x: -x[1]):
                print(f"    {lg}: {cnt}")

        # 测试3: 检查 _get_paginated 的 page 2
        if pagination2.get("has_more"):
            print("\n" + "=" * 60)
            print("测试3: 拉取 page 2")
            params3 = {"api_token": API_KEY, "include": "league;participants", "per_page": 200, "page": 2}
            resp3 = await client.get("/fixtures/between/2026-07-28/2026-08-05", params=params3)
            data3 = resp3.json()
            fixtures3 = data3.get("data", [])
            pagination3 = data3.get("pagination", {})
            print(f"  page 2: {len(fixtures3)} fixtures, has_more={pagination3.get('has_more')}, total={pagination3.get('count')}")
            if fixtures3:
                leagues3 = {}
                for fx in fixtures3:
                    lg = fx.get("league", {})
                    lg_id = lg.get("id", "?") if isinstance(lg, dict) else fx.get("league_id", "?")
                    lg_name = lg.get("name", "?") if isinstance(lg, dict) else "?"
                    key = f"{lg_name}(id={lg_id})"
                    leagues3[key] = leagues3.get(key, 0) + 1
                for lg, cnt in sorted(leagues3.items(), key=lambda x: -x[1]):
                    print(f"    {lg}: {cnt}")

        # 测试4: 直接搜索关键联赛的 fixtures
        print("\n" + "=" * 60)
        print("测试4: 搜索特定联赛 fixtures")

        # 已知的SM联赛ID猜测:
        # 试试按国家搜索 - 芬兰、瑞典、挪威、美国
        for country_id, country_name in [(66, "Finland"), (33, "Sweden"), (22, "Norway"), (11, "USA")]:
            params4 = {"api_token": API_KEY, "include": "league", "per_page": 3}
            resp4 = await client.get(f"/leagues/countries/{country_id}", params=params4)
            await asyncio.sleep(2)  # rate limit
            data4 = resp4.json()
            leagues4 = data4.get("data", [])
            if leagues4:
                print(f"  {country_name}: {len(leagues4)} leagues")
                for lg in leagues4[:5]:
                    lg_id = lg.get("id")
                    lg_name = lg.get("name")
                    print(f"    - id={lg_id} name={lg_name}")
            else:
                print(f"  {country_name}: 无数据")

        # 测试5: 直接用 fixture ID 查找 (测试单个存在的fixture)
        print("\n" + "=" * 60)
        print("测试5: 查单个fixture的odds")
        # 莫尔德vs萨普斯堡 fixture_id=19629615
        params5 = {"api_token": API_KEY}
        resp5 = await client.get("/fixtures/19629615", params=params5)
        await asyncio.sleep(2)
        data5 = resp5.json()
        fx5 = data5.get("data", {})
        if fx5:
            lg = fx5.get("league", {})
            print(f"  fixture_id=19629615: name={fx5.get('name')}, league_id={fx5.get('league_id')}")
            print(f"    league detail: {lg.get('id')} - {lg.get('name') if isinstance(lg, dict) else '?'}")

        # 测试6: 查odds endpoint
        print("\n" + "=" * 60)
        print("测试6: 直接查odds for fixture 19629615")
        params6 = {"api_token": API_KEY}
        resp6 = await client.get("/odds/pre-match/fixtures/19629615", params=params6)
        await asyncio.sleep(2)
        data6 = resp6.json()
        odds6 = data6.get("data", [])
        print(f"  odds for 19629615: {len(odds6)} 条")
        if odds6:
            # 统计bookmaker
            bm_set = set()
            for o in odds6[:20]:
                bm = o.get("bookmaker", {})
                bm_name = bm.get("name", "?") if isinstance(bm, dict) else o.get("bookmaker_id", "?")
                bm_set.add(bm_name)
            print(f"    博彩公司: {bm_set}")


if __name__ == "__main__":
    asyncio.run(main())
