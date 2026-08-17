"""查 785/1635/1747/1748 引用 + SM 828/187 身份"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 785/1635/1747/1748 引用 ===")
    for tid in [785, 1635, 1747, 1748]:
        r = await conn.fetchrow(
            "SELECT id, name_zh, name_en, sportmonks_id, league_id, needs_review, "
            "(SELECT count(*) FROM matches WHERE home_team_id=teams.id) AS h, "
            "(SELECT count(*) FROM matches WHERE away_team_id=teams.id) AS a "
            "FROM teams WHERE id=$1", tid)
        print(f"  id={r['id']} zh={r['name_zh']} en={r['name_en']} sm={r['sportmonks_id']} lg={r['league_id']} review={r['needs_review']} 引用(h={r['h']},a={r['a']})")
        a = await conn.fetch("SELECT id, alias_name, is_primary FROM team_aliases WHERE team_id=$1", tid)
        s = await conn.fetch("SELECT id, season FROM team_season_stats WHERE team_id=$1", tid)
        print(f"      aliases={a if a else '无'} stats={s if s else '无'}")

    print("\n=== 785/1635 引用的比赛 ===")
    for tid in [785, 1635]:
        rows = await conn.fetch(
            """
            SELECT m.id, m.match_num, l.name_zh AS lg, m.kickoff_time,
                   th.name_zh AS hzh, ta.name_zh AS azh
            FROM matches m
            LEFT JOIN leagues l ON l.id = m.league_id
            LEFT JOIN teams th ON th.id = m.home_team_id
            LEFT JOIN teams ta ON ta.id = m.away_team_id
            WHERE m.home_team_id=$1 OR m.away_team_id=$1
            ORDER BY m.kickoff_time DESC LIMIT 15
            """, tid)
        print(f"  --- team {tid} ---")
        for r in rows:
            print(f"      #{r['id']} {r['match_num']} [{r['lg']}] {r['kickoff_time']} {r['hzh']} vs {r['azh']}")

    print("\n=== 15643 详情 ===")
    r = await conn.fetchrow(
        "SELECT id, home_team_id, away_team_id, home_team_name, away_team_name, sportmonks_fixture_id AS fx, is_swapped, venue FROM matches WHERE id=15643")
    print(f"  {dict(r)}")

    await conn.close()


asyncio.run(main())
