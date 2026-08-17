"""08-14 竞彩周期（周四）完整范围检查 —— 含 08-15 凌晨场次"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    # 1) 周四竞彩周期的所有比赛（含 08-15 凌晨）
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.kickoff_time, l.name_zh AS lg,
               th.id AS hid, th.name_zh AS hzh, th.name_en AS hen,
               th.sportmonks_id AS hsm, th.needs_review AS hnr,
               ta.id AS aid, ta.name_zh AS azh, ta.name_en AS aen,
               ta.sportmonks_id AS asm, ta.needs_review AS anr,
               m.sportmonks_fixture_id AS fx
        FROM matches m
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.match_num LIKE '周四%'
        ORDER BY m.kickoff_time
        """)
    print(f"=== 周四竞彩周期共 {len(rows)} 场 ===")
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
        line = (f"  #{r['id']} [{r['lg']}] {r['match_num']} {r['hzh'] or r['hen']}"
                f"(sm={r['hsm']}) vs {r['azh'] or r['aen']}(sm={r['asm']}) "
                f"ko={r['kickoff_time']} fx={r['fx']}")
        if flags:
            line += f"  <<< {','.join(flags)}"
            problem.append((r, flags))
        print(line)

    # 2) 顺便看 08-15 全天所有比赛（判断是否有非周四周期但时间在 08-15 的）
    rows15 = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.kickoff_time, l.name_zh AS lg
        FROM matches m JOIN leagues l ON l.id=m.league_id
        WHERE m.kickoff_time >= '2026-08-15 00:00:00' AND m.kickoff_time < '2026-08-16 00:00:00'
        ORDER BY m.kickoff_time
        """)
    print(f"\n=== 08-15 全天比赛 {len(rows15)} 场 ===")
    for r in rows15:
        print(f"  #{r['id']} [{r['lg']}] {r['match_num']} ko={r['kickoff_time']}")

    print(f"\n=== 问题汇总：{len(problem)} 场存在潜在问题 ===")
    await conn.close()


asyncio.run(main())
