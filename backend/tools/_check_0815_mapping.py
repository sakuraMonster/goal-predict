"""08-15 比赛日球队映射检查（含 08-15 凌晨跨周期场次）"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.kickoff_time, l.name_zh AS lg,
               th.id AS hid, th.name_zh AS hzh, th.name_en AS hen,
               th.sportmonks_id AS hsm, th.needs_review AS hnr,
               ta.id AS aid, ta.name_zh AS azh, ta.name_en AS aen,
               ta.sportmonks_id AS asm, ta.needs_review AS anr,
               m.sportmonks_fixture_id AS fx,
               m.home_team_name AS hraw, m.away_team_name AS araw
        FROM matches m
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.kickoff_time >= '2026-08-15 00:00:00' AND m.kickoff_time < '2026-08-16 00:00:00'
        ORDER BY m.kickoff_time
        """)
    print(f"=== 08-15 比赛日共 {len(rows)} 场 ===")
    problem = []
    for r in rows:
        flags = []
        if not r["hzh"] or not r["azh"]:
            flags.append("缺中文名")
        if r["hnr"] or r["anr"]:
            flags.append("needs_review")
        if r["hsm"] is None or r["asm"] is None:
            flags.append("无SM_id")
        if r["fx"] is None:
            flags.append("无fixture")
        line = (f"  #{r['id']} [{r['lg']}] {r['match_num']} {r['hzh'] or r['hen']}(id={r['hid']},sm={r['hsm']}) "
                f"vs {r['azh'] or r['aen']}(id={r['aid']},sm={r['asm']}) "
                f"ko={r['kickoff_time']} fx={r['fx']}")
        if flags:
            line += f"  <<< {','.join(flags)}"
            problem.append((r, flags))
        print(line)

    print(f"\n=== 问题汇总：{len(problem)} 场存在潜在问题 ===")
    await conn.close()


asyncio.run(main())
