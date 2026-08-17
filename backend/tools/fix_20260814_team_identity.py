"""修复 2026-08-14 球队身份污染：
1. team 119/116 是日职球队（柏太阳神/东京绿茵），name_zh 被污染为沙特名 → 还原
2. team 443/457 是沙特球队（Al Shabab=利雅得青年 / Al-Qadsiah=胡巴尔卡德西亚），补中文名
3. match 15595（沙职 08-14 02:00）错误引用 119/116 + 误用 J1 fixture 19719021 → 修正
4. prediction 10585 的比分来自错误 J1 fixture → 清理

身份依据（双向验证）：
- fx=19719021 (SM 实测) = Tokyo Verdy vs Kashiwa Reysol, 08-14 10:00 UTC = 北京 18:00，
  与 match 15604（日职, home=116 away=119, 18:00）完全吻合 → 116=东京绿茵, 119=柏太阳神
- 2025 年引用 119/116 的比赛（league_id=7 日职联）对手全是 J1 球队
  （福冈黄蜂/川崎前锋/大阪樱花/浦和红钻/鹿岛鹿角等）→ 印证
- 443/457 的 2026 年比赛对手全是沙特队（Al Ittihad/Al Hilal/Damac/Al Taawoun 等）
  → 443=Al Shabab=利雅得青年, 457=Al-Qadsiah=胡巴尔卡德西亚
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    # ── 1. 预检 ──
    checks = {
        "team119": await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id, league_id FROM teams WHERE id=119"),
        "team116": await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id, league_id FROM teams WHERE id=116"),
        "team443": await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id, league_id FROM teams WHERE id=443"),
        "team457": await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id, league_id FROM teams WHERE id=457"),
        "alias880": await conn.fetchrow("SELECT id, alias_name, team_id, is_primary FROM team_aliases WHERE id=880"),
        "alias881": await conn.fetchrow("SELECT id, alias_name, team_id, is_primary FROM team_aliases WHERE id=881"),
        "match15595": await conn.fetchrow(
            "SELECT id, venue, home_team_id, away_team_id, league_id, sportmonks_fixture_id "
            "FROM matches WHERE id=15595"),
        "pred10585": await conn.fetchrow("SELECT id, match_id FROM predictions WHERE id=10585"),
    }
    ok = True
    for k, v in checks.items():
        print(f"预检 {k}:", dict(v) if v else None)
        if v is None:
            ok = False
    # 断言关键前提
    if (checks["team119"]["name_zh"] != "利雅得青年"
            or checks["team119"]["name_en"] != "Kashiwa Reysol"
            or checks["team119"]["sportmonks_id"] != 298):
        print("预检失败：team119 身份前提不满足，中止")
        ok = False
    if (checks["team116"]["name_zh"] != "胡巴尔卡德西亚"
            or checks["team116"]["name_en"] != "Tokyo Verdy"
            or checks["team116"]["sportmonks_id"] != 2691):
        print("预检失败：team116 身份前提不满足，中止")
        ok = False
    if (checks["match15595"]["home_team_id"] != 119
            or checks["match15595"]["away_team_id"] != 116
            or checks["match15595"]["sportmonks_fixture_id"] != 19719021):
        print("预检失败：match15595 前提不满足，中止")
        ok = False
    if checks["team443"]["sportmonks_id"] != 16184 or checks["team457"]["sportmonks_id"] != 13092:
        print("预检失败：443/457 sm_id 前提不满足，中止")
        ok = False
    if not ok:
        await conn.close()
        return

    # ── 2. 执行 ──
    async with conn.transaction():
        # 2.1 还原 119/116 中文名（日职身份）
        await conn.execute("UPDATE teams SET name_zh='柏太阳神' WHERE id=119")
        await conn.execute("UPDATE teams SET name_zh='东京绿茵' WHERE id=116")
        # 2.2 删除污染别名（沙特名挂在日职队上）
        await conn.execute("DELETE FROM team_aliases WHERE id IN (880, 881)")
        # 2.3 443/457 补中文名
        await conn.execute("UPDATE teams SET name_zh='利雅得青年' WHERE id=443")
        await conn.execute("UPDATE teams SET name_zh='胡巴尔卡德西亚' WHERE id=457")
        # 2.4 443/457 添加 sporttery 主别名（保证竞彩后续 sync 能匹配到正确队）
        await conn.execute(
            "INSERT INTO team_aliases (team_id, alias_name, source, is_primary, league_name_zh) "
            "VALUES (443, '利雅得青年', 'sporttery.cn', true, '沙职'), "
            "(457, '胡巴尔卡德西亚', 'sporttery.cn', true, '沙职')")
        # 2.5 match 15595 修正球队 + 清除错误 fixture
        await conn.execute(
            "UPDATE matches SET home_team_id=443, away_team_id=457, sportmonks_fixture_id=NULL "
            "WHERE id=15595")
        # 2.6 prediction 10585 清理错误比分（来自 J1 fixture 19719021）
        await conn.execute(
            "UPDATE predictions SET actual_home_score=NULL, actual_away_score=NULL, "
            "actual_total_goals=NULL, actual_score=NULL, result_spf=NULL, result_hcp=NULL, "
            "result_goals=NULL, result_score=NULL WHERE id=10585")

    # ── 3. 复验 ──
    print("\n复验:")
    for tid in (119, 116, 443, 457):
        t = await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE id=$1", tid)
        print(f"  team {tid}:", dict(t))
    m = await conn.fetchrow(
        "SELECT id, home_team_id, away_team_id, sportmonks_fixture_id FROM matches WHERE id=15595")
    print("  match 15595:", dict(m))
    al = await conn.fetch("SELECT id, team_id, alias_name, is_primary FROM team_aliases WHERE id IN (880,881)")
    print("  污染别名残留:", [dict(a) for a in al])
    al2 = await conn.fetch("SELECT team_id, alias_name, source, is_primary FROM team_aliases WHERE team_id IN (443,457)")
    print("  443/457 别名:", [dict(a) for a in al2])
    p = await conn.fetchrow("SELECT id, actual_score, actual_total_goals FROM predictions WHERE id=10585")
    print("  prediction 10585:", dict(p))
    dup = await conn.fetch(
        "SELECT sportmonks_fixture_id, count(*) FROM matches "
        "WHERE sportmonks_fixture_id IS NOT NULL GROUP BY sportmonks_fixture_id HAVING count(*)>1")
    print("  fx 重复:", [dict(r) for r in dup])

    await conn.close()
    print("done")


asyncio.run(main())
