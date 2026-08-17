"""补查 1590/1608 别名 + 引用比赛 + 引用删除前需清理的外键，写 UTF-8 文件"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_1590b.txt"


async def main():
    conn = await asyncpg.connect(DSN)
    lines = []

    def p(s=""):
        lines.append(str(s))
        print(s)

    p("=== team_aliases 1590 / 1608 ===")
    rows = await conn.fetch(
        "SELECT id, team_id, alias_name, is_primary, source FROM team_aliases WHERE team_id IN (1590,1608) ORDER BY team_id, id"
    )
    for r in rows:
        p(f"  #{r['id']} team={r['team_id']} alias={r['alias_name']!r} primary={r['is_primary']} src={r['source']}")

    p("\n=== teams 1590 / 1608 全字段 ===")
    rows = await conn.fetch(
        "SELECT id, name_zh, name_en, sportmonks_id, league_id FROM teams WHERE id IN (1590,1608)"
    )
    for r in rows:
        p(f"  id={r['id']} name_zh={r['name_zh']!r} name_en={r['name_en']!r} sm={r['sportmonks_id']} lg={r['league_id']}")

    p("\n=== 引用 1608 的比赛 ===")
    rows = await conn.fetch(
        "SELECT id, league_id, kickoff_time, home_team_id, away_team_id, sportmonks_fixture_id AS fx, status FROM matches WHERE home_team_id=1608 OR away_team_id=1608 ORDER BY kickoff_time"
    )
    for r in rows:
        p(f"  #{r['id']} ko={r['kickoff_time']} lg={r['league_id']} {r['home_team_id']} vs {r['away_team_id']} fx={r['fx']} status={r['status']}")

    p("\n=== 引用 1590 的比赛 ===")
    rows = await conn.fetch(
        "SELECT id, league_id, kickoff_time, home_team_id, away_team_id, sportmonks_fixture_id AS fx, status FROM matches WHERE home_team_id=1590 OR away_team_id=1590 ORDER BY kickoff_time"
    )
    for r in rows:
        p(f"  #{r['id']} ko={r['kickoff_time']} lg={r['league_id']} {r['home_team_id']} vs {r['away_team_id']} fx={r['fx']} status={r['status']}")

    p("\n=== 1608 的 team_season_stats / head_to_head 引用 ===")
    rows = await conn.fetch("SELECT id, season, league_id, played, form FROM team_season_stats WHERE team_id=1608")
    for r in rows:
        p(f"  stats #{r['id']} season={r['season']} lg={r['league_id']} played={r['played']} form={r['form']}")
    rows = await conn.fetch(
        "SELECT id, match_date, home_team_id, away_team_id, home_score, away_score FROM head_to_head WHERE home_team_id=1608 OR away_team_id=1608 ORDER BY match_date DESC"
    )
    for r in rows:
        p(f"  h2h #{r['id']} {r['match_date'].date()} {r['home_team_id']}:{r['away_team_id']} {r['home_score']}-{r['away_score']}")

    p("\n=== 1590 的 team_season_stats 明细 ===")
    rows = await conn.fetch(
        "SELECT id, season, league_id, played, wins, draws, losses, goals_for, goals_against, form FROM team_season_stats WHERE team_id=1590"
    )
    for r in rows:
        p(f"  #{r['id']} season={r['season']} lg={r['league_id']} played={r['played']} w/d/l={r['wins']}/{r['draws']}/{r['losses']} gf/ga={r['goals_for']}/{r['goals_against']} form={r['form']}")

    p("\n=== 1608 recent_matches 首条/末条 ===")
    rows = await conn.fetch(
        "SELECT id, recent_matches FROM team_season_stats WHERE team_id=1608"
    )
    for r in rows:
        rm = r["recent_matches"]
        if rm:
            if isinstance(rm, str):
                rm = rm.replace("'", '"')
            p(f"  stats#{r['id']} recent_matches={rm[:500]}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[written] {OUT}")

    await conn.close()


asyncio.run(main())
