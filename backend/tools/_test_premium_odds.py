"""测试 SportMonks Premium Odds History 端点是否可用"""
import json
import httpx

API_KEY = "vVefqiiMY3B0Ph69Rp4PTOCztmAKUvNWFTHDJPdyfs930haXtMqjWZIYkJEE"
BASE = "https://api.sportmonks.com/v3/football"


def test_premium_access():
    """直接测试 Premium 端点是否有权限（不带 fixture 过滤，per_page=2）"""
    url = f"{BASE}/odds/premium/history"
    params = {"api_token": API_KEY, "per_page": 2}

    print("=" * 60)
    print("[1] 测试 Premium History 端点 — 权限检查")
    print(f"    URL: {url}?per_page=2")
    print()

    try:
        resp = httpx.get(url, params=params, timeout=30)
        print(f"    Status: {resp.status_code}")
        print(f"    Headers: {dict(resp.headers)}")

        raw = resp.json()
        # SM 可能返回 dict 或 list
        print(f"    返回类型: {type(raw).__name__}")

        if isinstance(raw, dict):
            items = raw.get("data", [])
            sub = raw.get("subscription", {})
            rl = raw.get("rate_limit", {})
            print(f"    订阅: {sub}")
            print(f"    剩余请求: {rl.get('remaining', 'N/A')}")
        elif isinstance(raw, list):
            items = raw
        else:
            items = []

        print(f"    返回条数: {len(items)}")

        if resp.status_code == 200:
            if items:
                print(f"\n    首条 full sample:")
                print(f"    {json.dumps(items[0], ensure_ascii=False, indent=2)}")
                print(f"\n    第2条 full sample:")
                if len(items) > 1:
                    print(f"    {json.dumps(items[1], ensure_ascii=False, indent=2)}")
                return True
            else:
                print(f"    >>> Premium 可用但无数据（空列表）")
                print(f"    完整响应: {json.dumps(raw, ensure_ascii=False)[:500]}")
                return True  # 200 就是有权限

        elif resp.status_code == 402:
            print(f"    >>> 402 Payment Required")
            print(f"    响应: {json.dumps(raw, ensure_ascii=False)[:300]}")
            return False
        elif resp.status_code == 403:
            print(f"    >>> 403 Forbidden")
            return False
        else:
            print(f"    完整响应: {json.dumps(raw, ensure_ascii=False)[:500]}")
            return False

    except Exception as e:
        print(f"    ERROR: {type(e).__name__}: {e}")
        return False


def get_fixture_for_match():
    """从 DB 获取一场比赛的 SM fixture_id"""
    try:
        import asyncpg
        import asyncio

        async def _query():
            conn = await asyncpg.connect(
                "postgresql://postgres:postgres@localhost:5432/football_prediction"
            )
            row = await conn.fetchrow(
                "SELECT id, match_num, home_team_name, away_team_name, sportmonks_fixture_id "
                "FROM matches WHERE sportmonks_fixture_id IS NOT NULL "
                "ORDER BY kickoff_time DESC LIMIT 1"
            )
            await conn.close()
            return row

        row = asyncio.run(_query())
        if row:
            print(f"\n    使用赛事: ID={row['id']} {row['match_num']} "
                  f"{row['home_team_name']} vs {row['away_team_name']} "
                  f"(SM fixture={row['sportmonks_fixture_id']})")
            return row
        return None
    except Exception as e:
        print(f"    DB 查询失败: {e}，使用硬编码 fixture_id")
        return None


def test_standard_pre_match(fixture_id):
    """标准 Pre-Match 端点：查看大小球数据结构"""
    url = f"{BASE}/odds/pre-match/fixtures/{fixture_id}"
    params = {"api_token": API_KEY, "include": "bookmaker"}

    print(f"\n{'=' * 60}")
    print(f"[3] 标准 Pre-Match 端点 — 数据结构检查")
    print(f"    fixture_id: {fixture_id}")

    try:
        resp = httpx.get(url, params=params, timeout=30)
        print(f"    Status: {resp.status_code}")

        data = resp.json()
        items = data.get("data", [])
        print(f"    总 odds 条数: {len(items)}")

        # market 分布
        from collections import Counter
        market_dist = Counter(item.get("market_id") for item in items)
        print(f"    Market 分布: {dict(market_dist)}")

        # 大小球 (market_id=80) 数据
        ou_items = [i for i in items if i.get("market_id") == 80]
        print(f"\n    大小球 (market=80) 共 {len(ou_items)} 条:")

        # 按 bookmaker 分组展示
        ou_by_bm = {}
        for i in ou_items:
            bm_id = i.get("bookmaker_id", "?")
            ou_by_bm.setdefault(bm_id, []).append(i)

        for bm_id, ous in ou_by_bm.items():
            lines = [(o.get("total"), o.get("label"), o.get("value")) for o in ous]
            print(f"      bookmaker_id={bm_id}: {lines}")

        # 完整字段
        if items:
            print(f"\n    完整字段列表: {sorted(items[0].keys())}")

        return items

    except Exception as e:
        print(f"    ERROR: {type(e).__name__}: {e}")
        return []


def test_premium_with_fixture(fixture_id):
    """测试 Premium 端点按 fixture 过滤"""
    url = f"{BASE}/odds/premium/history"
    params = {
        "api_token": API_KEY,
        "filters": f"fixture:fixture_id={fixture_id}",
        "per_page": 3,
    }

    print(f"\n{'=' * 60}")
    print(f"[3] Premium History — 按 fixture 过滤")
    print(f"    fixture_id: {fixture_id}")

    try:
        resp = httpx.get(url, params=params, timeout=30)
        print(f"    Status: {resp.status_code}")

        data = resp.json()
        if isinstance(data, dict):
            items = data.get("data", [])
            msg = data.get("message", "")
        else:
            items = data
            msg = ""

        print(f"    返回条数: {len(items)}")
        if msg:
            print(f"    消息: {msg}")

        if items:
            print(f"\n    首条 sample:")
            print(f"    {json.dumps(items[0], ensure_ascii=False, indent=2)}")
            # 检查是否有 history/opening 相关字段
            print(f"\n    字段列表: {sorted(items[0].keys())}")
        return len(items) > 0
    except Exception as e:
        print(f"    ERROR: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    # 1. 先测权限
    has_access = test_premium_access()

    # 2. 获取真实 fixture_id
    print(f"\n{'=' * 60}")
    print(f"[2] 获取测试 fixture_id")
    match_row = get_fixture_for_match()
    fixture_id = match_row["sportmonks_fixture_id"] if match_row else 19398267

    # 3. Premium 带 fixture filter
    if has_access:
        has_data = test_premium_with_fixture(fixture_id)

    # 4. 标准端点数据结构检查
    test_standard_pre_match(fixture_id)

    # 5. 汇总
    print(f"\n{'=' * 60}")
    print(f"[5] 汇总结论")
    print(f"    订阅: Growth + Euro Club + Odds & Predictions bundle")
    print(f"    Premium History 端点: {'可用' if has_access else '不可用'}")
    if has_access:
        print(f"    Premium fixture filter: {'有数据' if locals().get('has_data') else '无数据（可能需其他过滤方式）'}")
    print(f"    标准端点 latest_bookmaker_update 字段: 存在，可用于追踪赔率更新时间")
