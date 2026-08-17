"""合并 1608→1590 前置检查：所有引用 1608 的外键记录"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_merge_check.txt"


async def main():
    conn = await asyncpg.connect(DSN)
    lines = []

    def p(s=""):
        lines.append(str(s))
        print(s)

    # 1. matches
    p("=== matches 引用 1608 ===")
    rows = await conn.fetch(
        "SELECT id, kickoff_time, home_team_id, away_team_id, sportmonks_fixture_id AS fx, status FROM matches WHERE home_team_id=1608 OR away_team_id=1608"
    )
    for r in rows:
        p(f"  #{r['id']} ko={r['kickoff_time']} {r['home_team_id']} vs {r['away_team_id']} fx={r['fx']} status={r['status']}")

    # 2. predictions
    p("\n=== predictions 引用 1608 ===")
    try:
        rows = await conn.fetch(
            "SELECT id, match_id, home_team_id, away_team_id, league_id FROM predictions WHERE home_team_id=1608 OR away_team_id=1608"
        )
        for r in rows:
            p(f"  #{r['id']} match={r['match_id']} {r['home_team_id']} vs {r['away_team_id']} lg={r['league_id']}")
        if not rows:
            p("  (无)")
    except Exception as e:
        p(f"  ERROR: {e}")

    # 3. head_to_head
    p("\n=== head_to_head 引用 1608 ===")
    rows = await conn.fetch(
        "SELECT id, match_date, home_team_id, away_team_id, home_score, away_score FROM head_to_head WHERE home_team_id=1608 OR away_team_id=1608"
    )
    for r in rows:
        p(f"  #{r['id']} {r['match_date'].date()} {r['home_team_id']}:{r['away_team_id']} {r['home_score']}-{r['away_score']}")
    if not rows:
        p("  (无)")

    # 4. team_aliases
    p("\n=== team_aliases 引用 1608 ===")
    rows = await conn.fetch(
        "SELECT id, team_id, alias_name, is_primary, source FROM team_aliases WHERE team_id=1608"
    )
    for r in rows:
        p(f"  #{r['id']} alias={r['alias_name']!r} primary={r['is_primary']} src={r['source']}")

    # 5. team_season_stats
    p("\n=== team_season_stats 引用 1608 ===")
    rows = await conn.fetch(
        "SELECT id, season, league_id, played, form FROM team_season_stats WHERE team_id=1608"
    )
    for r in rows:
        p(f"  #{r['id']} season={r['season']} lg={r['league_id']} played={r['played']} form={r['form']}")

    # 6. 1590 现有中文名/别名
    p("\n=== 1590 现有状态 ===")
    r = await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE id=1590")
    p(f"  1590: zh={r['name_zh']!r} en={r['name_en']!r} sm={r['sportmonks_id']}")
    rows = await conn.fetch(
        "SELECT id, alias_name, is_primary, source FROM team_aliases WHERE team_id=1590"
    )
    for r in rows:
        p(f"  alias #{r['id']} {r['alias_name']!r} primary={r['is_primary']} src={r['source']}")
    if not rows:
        p("  (无别名)")

    # 7. 全局是否已有 sporttery 主别名 '利勒斯特罗姆' 挂别的队
    p("\n=== 别名 '利勒斯特罗姆' 全局归属 ===")
    rows = await conn.fetch(
        "SELECT id, team_id, alias_name, is_primary, source FROM team_aliases WHERE alias_name='利勒斯特罗姆'"
    )
    for r in rows:
        p(f"  #{r['id']} team={r['team_id']} {r['alias_name']!r} primary={r['is_primary']} src={r['source']}")
    if not rows:
        p("  (无)")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    p(f"\n[written] {OUT}")
    await conn.close()


asyncio.run(main())
