"""检查 SM id 占用情况 + 周日比赛映射状态"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

# 需检查的 SM id
CHECK_IDS = [35867, 269225, 1652, 185, 2831, 3317, 18263, 17798]


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== SM id 占用检查 ===")
    for smid in CHECK_IDS:
        t = await conn.fetchrow(
            "SELECT id, name_zh, name_en, league_id, needs_review FROM teams WHERE sportmonks_id=$1", smid)
        if t:
            print(f"  SM {smid} -> 已占用: id={t['id']} zh={t['name_zh']} en={t['name_en']} lg={t['league_id']} review={t['needs_review']}")
        else:
            print(f"  SM {smid} -> 未占用")

    print("\n=== 待修复球队详情 ===")
    rows = await conn.fetch(
        """
        SELECT id, name_zh, name_en, sportmonks_id, league_id, needs_review,
               (SELECT count(*) FROM matches WHERE home_team_id=teams.id) AS h_cnt,
               (SELECT count(*) FROM matches WHERE away_team_id=teams.id) AS a_cnt
        FROM teams WHERE id IN (402, 346, 1744, 1745, 1746)
        """
    )
    for r in rows:
        print(f"  id={r['id']} zh={r['name_zh']} en={r['name_en']} sm={r['sportmonks_id']} lg={r['league_id']} review={r['needs_review']} 引用(h={r['h_cnt']},a={r['a_cnt']})")

    print("\n=== 346/402 引用的比赛（判断其真实身份）===")
    rows2 = await conn.fetch(
        """
        SELECT m.id, m.match_num, l.name_zh AS lg, m.kickoff_time,
               th.name_zh AS hzh, ta.name_zh AS azh
        FROM matches m
        LEFT JOIN leagues l ON l.id = m.league_id
        LEFT JOIN teams th ON th.id = m.home_team_id
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.home_team_id IN (346,402) OR m.away_team_id IN (346,402)
        ORDER BY m.kickoff_time DESC LIMIT 20
        """
    )
    for r in rows2:
        print(f"  #{r['id']} {r['match_num']} [{r['lg']}] {r['kickoff_time']} {r['hzh']} vs {r['azh']}")

    print("\n=== 周日比赛（08-16 12:00 ~ 08-17 12:00）映射状态 ===")
    rows3 = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.kickoff_time, l.name_zh AS lg, m.sportmonks_fixture_id AS fx,
               th.name_zh AS hzh, ta.name_zh AS azh,
               th.sportmonks_id AS hsm, ta.sportmonks_id AS asm
        FROM matches m
        LEFT JOIN leagues l ON l.id = m.league_id
        LEFT JOIN teams th ON th.id = m.home_team_id
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.kickoff_time >= '2026-08-16 12:00:00' AND m.kickoff_time < '2026-08-17 12:00:00'
        ORDER BY m.kickoff_time
        """
    )
    for r in rows3:
        mark = "" if r["fx"] else "  <<< 无fx"
        print(f"  #{r['id']} {r['match_num']} [{r['lg']}] {r['kickoff_time']} {r['hzh']}(sm={r['hsm']}) vs {r['azh']}(sm={r['asm']}) fx={r['fx']}{mark}")

    await conn.close()


asyncio.run(main())
