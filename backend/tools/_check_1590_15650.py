"""检查 1590/1608 别名与统计 + 15650 现有 H2H 是否带 stats（决定能否被重同步覆盖）"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== team_aliases for 1590 / 1608 ===")
    rows = await conn.fetch(
        "SELECT id, team_id, alias_name, is_primary, source FROM team_aliases WHERE team_id IN (1590,1608)"
    )
    for r in rows:
        print(f"  #{r['id']} team={r['team_id']} alias={r['alias_name']} primary={r['is_primary']} src={r['source']}")

    print("\n=== 1590 的 team_season_stats ===")
    rows = await conn.fetch(
        "SELECT id, season, played, wins, draws, losses, goals_for, goals_against, form, league_id FROM team_season_stats WHERE team_id=1590"
    )
    for r in rows:
        print(f"  #{r['id']} season={r['season']} played={r['played']} w/d/l={r['wins']}/{r['draws']}/{r['losses']} gf/ga={r['goals_for']}/{r['goals_against']} form={r['form']} lg={r['league_id']}")

    print("\n=== 1590 参与的 H2H ===")
    rows = await conn.fetch(
        "SELECT id, match_date, home_team_id, away_team_id, home_score, away_score, sportmonks_fixture_id AS fx, home_stats FROM head_to_head WHERE home_team_id=1590 OR away_team_id=1590 ORDER BY match_date DESC LIMIT 10"
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_date'].date()} {r['home_team_id']}:{r['away_team_id']} {r['home_score']}-{r['away_score']} fx={r['fx']} home_stats={'Y' if r['home_stats'] else 'N'}")

    print("\n=== 15650 (米亚尔比157 vs 天狼星158) 现有 H2H ===")
    rows = await conn.fetch(
        "SELECT id, match_date, home_team_id, away_team_id, home_score, away_score, sportmonks_fixture_id AS fx, home_stats FROM head_to_head WHERE (home_team_id=157 AND away_team_id=158) OR (home_team_id=158 AND away_team_id=157) ORDER BY match_date DESC"
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_date'].date()} {r['home_team_id']}:{r['away_team_id']} {r['home_score']}-{r['away_score']} fx={r['fx']} home_stats={'Y' if r['home_stats'] else 'N'}")

    print("\n=== 全局 sm=2510 / sm=269 唯一性 ===")
    rows = await conn.fetch(
        "SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE sportmonks_id IN (2510,269)"
    )
    for r in rows:
        print(f"  id={r['id']} name_zh={r['name_zh']} name_en={r['name_en']} sm={r['sportmonks_id']}")

    await conn.close()


asyncio.run(main())
