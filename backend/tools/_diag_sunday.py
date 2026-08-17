"""查证周日 15637/15644 球队 + 346/402 引用 + 15643 swapped"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== SM id 占用（周日球队相关）===")
    for smid in [639, 5402, 828, 187, 7035, 1198, 269225]:
        t = await conn.fetchrow(
            "SELECT id, name_zh, name_en, league_id, needs_review FROM teams WHERE sportmonks_id=$1", smid)
        print(f"  SM {smid}: {dict(t) if t else '未占用'}")

    print("\n=== 15637/15644 当前引用球队 ===")
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.home_team_id, m.away_team_id, m.sportmonks_fixture_id AS fx, m.is_swapped
        FROM matches m WHERE m.id IN (15637, 15644)
        """
    )
    for r in rows:
        print(f"  #{r['id']} home={r['home_team_id']} away={r['away_team_id']} fx={r['fx']} swapped={r['is_swapped']}")

    print("\n=== 346 的别名与统计引用 ===")
    rows = await conn.fetch("SELECT id, alias_name, is_primary FROM team_aliases WHERE team_id=346")
    print(f"  aliases: {rows if rows else '无'}")
    rows = await conn.fetch("SELECT id, season, league_id FROM team_season_stats WHERE team_id=346")
    print(f"  season_stats: {rows if rows else '无'}")

    print("\n=== 402 的别名 ===")
    rows = await conn.fetch("SELECT id, alias_name, is_primary FROM team_aliases WHERE team_id=402")
    print(f"  aliases: {rows if rows else '无'}")

    print("\n=== 15643 is_swapped 确认 ===")
    r = await conn.fetchrow("SELECT id, home_team_id, away_team_id, sportmonks_fixture_id AS fx, is_swapped FROM matches WHERE id=15643")
    print(f"  {dict(r) if r else '无'}")

    print("\n=== 1744/1745/1746 的别名与统计引用 ===")
    for tid in [1744, 1745, 1746]:
        a = await conn.fetch("SELECT id, alias_name FROM team_aliases WHERE team_id=$1", tid)
        s = await conn.fetch("SELECT id, season, league_id FROM team_season_stats WHERE team_id=$1", tid)
        h = await conn.fetch("SELECT id FROM head_to_head WHERE home_team_id=$1 OR away_team_id=$1", tid)
        print(f"  team {tid}: aliases={a} stats={s} h2h={h}")

    print("\n=== 1697/1698/301/444 引用确认 ===")
    for tid in [1697, 1698, 301, 444]:
        r = await conn.fetchrow(
            "SELECT id, name_zh, name_en, sportmonks_id, league_id, "
            "(SELECT count(*) FROM matches WHERE home_team_id=teams.id) AS h, "
            "(SELECT count(*) FROM matches WHERE away_team_id=teams.id) AS a "
            "FROM teams WHERE id=$1", tid)
        print(f"  id={r['id']} zh={r['name_zh']} en={r['name_en']} sm={r['sportmonks_id']} lg={r['league_id']} 引用(h={r['h']},a={r['a']})")

    await conn.close()


asyncio.run(main())
