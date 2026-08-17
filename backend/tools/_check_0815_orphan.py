"""确认 08-15 当前映射状态 + 孤儿占位记录(1723~1741)引用情况"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 08-15 11 场比赛当前映射状态 ===")
    rows = await conn.fetch(
        """
        SELECT m.id, l.name_zh AS lg,
               th.name_zh AS hzh, th.sportmonks_id AS hsm,
               ta.name_zh AS azh, ta.sportmonks_id AS asm,
               m.sportmonks_fixture_id AS fx
        FROM matches m
        JOIN teams th ON th.id=m.home_team_id
        JOIN teams ta ON ta.id=m.away_team_id
        JOIN leagues l ON l.id=m.league_id
        WHERE m.id IN (15606,15607,15608,15611,15612,15614,15615,15616,15617,15618,15619)
        ORDER BY m.kickoff_time
        """)
    bad = 0
    for r in rows:
        flag = "" if (r["hsm"] and r["asm"] and r["fx"]) else "  <<< 未映射"
        if flag:
            bad += 1
        print(f"  #{r['id']} [{r['lg']}] {r['hzh']}(sm={r['hsm']}) vs {r['azh']}(sm={r['asm']}) fx={r['fx']}{flag}")
    print(f"  未映射场次: {bad}")

    print("\n=== 占位记录 1723~1741 状态 ===")
    teams = await conn.fetch(
        "SELECT id, name_zh, name_en, sportmonks_id, league_id, needs_review FROM teams "
        "WHERE id BETWEEN 1723 AND 1741 ORDER BY id")
    for t in teams:
        # 引用数
        refs = await conn.fetchval(
            "SELECT count(*) FROM matches WHERE home_team_id=$1 OR away_team_id=$1", t["id"])
        alias = await conn.fetchval("SELECT count(*) FROM team_aliases WHERE team_id=$1", t["id"])
        print(f"  id={t['id']} zh={t['name_zh']} en={t['name_en']} sm={t['sportmonks_id']} "
              f"lg={t['league_id']} review={t['needs_review']} 比赛引用={refs} 别名={alias}")

    await conn.close()


asyncio.run(main())
