"""合并 1608(利勒斯特罗姆 sm=269=Denizlispor 错误映射) → 1590(sm=2510 Lillestrøm 正确记录)
- matches: 15567 home、15652 away 改指 1590
- team_aliases: #620 'LIS' 迁至 1590
- 1590 补中文名 + sporttery 主别名（防再次创建占位）
- 删除 1608 的污染 team_season_stats（Denizlispor 数据）
- 删除 1608
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_merge_1608.txt"


async def main():
    conn = await asyncpg.connect(DSN)
    lines = []

    def p(s=""):
        lines.append(str(s))
        print(s)

    async with conn.transaction():
        # 1. matches 改指 1590
        r = await conn.execute("UPDATE matches SET home_team_id=1590 WHERE home_team_id=1608")
        p(f"UPDATE matches.home_team_id 1608→1590: {r}")
        r = await conn.execute("UPDATE matches SET away_team_id=1590 WHERE away_team_id=1608")
        p(f"UPDATE matches.away_team_id 1608→1590: {r}")

        # 2. 迁移别名（1608 → 1590）
        r = await conn.execute("UPDATE team_aliases SET team_id=1590 WHERE team_id=1608")
        p(f"UPDATE team_aliases team 1608→1590: {r}")

        # 3. 1590 补中文名（原来 name_zh='Lillestrøm' 英文名，竞彩网用中文）
        r = await conn.execute("UPDATE teams SET name_zh='利勒斯特罗姆' WHERE id=1590")
        p(f"UPDATE teams 1590 name_zh→利勒斯特罗姆: {r}")

        # 4. 1590 补 sporttery 主别名（若不存在）
        exist = await conn.fetchrow(
            "SELECT id FROM team_aliases WHERE team_id=1590 AND alias_name='利勒斯特罗姆'"
        )
        if not exist:
            r = await conn.execute(
                "INSERT INTO team_aliases (team_id, alias_name, is_primary, source) VALUES (1590, '利勒斯特罗姆', true, 'sporttery.cn')"
            )
            p(f"INSERT sporttery 主别名 '利勒斯特罗姆' → 1590: {r}")
        else:
            p("sporttery 主别名 '利勒斯特罗姆' 已存在，跳过")

        # 5. 删除 1608 的污染 stats（Denizlispor 2002-2003 数据）
        r = await conn.execute("DELETE FROM team_season_stats WHERE team_id=1608")
        p(f"DELETE team_season_stats 1608: {r}")

        # 6. 删除 1608（别名/stats 已清理，matches 已改指）
        r = await conn.execute("DELETE FROM teams WHERE id=1608")
        p(f"DELETE teams 1608: {r}")

    # 验证
    p("\n=== 验证 ===")
    rows = await conn.fetch(
        "SELECT id, kickoff_time, home_team_id, away_team_id, sportmonks_fixture_id AS fx FROM matches WHERE id IN (15567,15652)"
    )
    for r in rows:
        p(f"  match #{r['id']} {r['home_team_id']} vs {r['away_team_id']} fx={r['fx']}")

    rows = await conn.fetch("SELECT id, team_id, alias_name, is_primary, source FROM team_aliases WHERE team_id=1590 ORDER BY id")
    for r in rows:
        p(f"  alias #{r['id']} team={r['team_id']} {r['alias_name']!r} primary={r['is_primary']} src={r['source']}")

    rows = await conn.fetch("SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE id=1590")
    for r in rows:
        p(f"  team 1590: zh={r['name_zh']!r} en={r['name_en']!r} sm={r['sportmonks_id']}")

    gone = await conn.fetchrow("SELECT id FROM teams WHERE id=1608")
    p(f"  1608 已删除: {gone is None}")

    rows = await conn.fetch(
        "SELECT sportmonks_id, COUNT(*) AS c FROM teams WHERE sportmonks_id IN (2510,269) GROUP BY sportmonks_id"
    )
    for r in rows:
        p(f"  sm={r['sportmonks_id']} count={r['c']}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    p(f"\n[written] {OUT}")
    await conn.close()


asyncio.run(main())
