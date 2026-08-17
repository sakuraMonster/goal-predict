"""08-15 比赛周期验证：比赛清单 + 球队映射 + 赔率快照统计
比赛周期定义：2026-08-15 12:00 ~ 2026-08-16 12:00（北京时间 naive）
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

SQL_MATCHES = """
SELECT m.id, m.jc_match_id, m.match_num, m.kickoff_time, m.status,
       m.sportmonks_fixture_id AS fx, m.home_team_name AS hraw,
       m.away_team_name AS araw, m.home_score, m.away_score, m.is_swapped,
       l.name_zh AS lg,
       th.id AS hid, th.name_zh AS hzh, th.name_en AS hen, th.sportmonks_id AS hsm,
       th.needs_review AS hnr,
       ta.id AS aid, ta.name_zh AS azh, ta.name_en AS aen, ta.sportmonks_id AS asm,
       ta.needs_review AS anr
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN teams th ON th.id = m.home_team_id
LEFT JOIN teams ta ON ta.id = m.away_team_id
WHERE m.kickoff_time >= '2026-08-15 12:00:00' AND m.kickoff_time < '2026-08-16 12:00:00'
ORDER BY m.kickoff_time
"""

SQL_ODDS = """
SELECT o.match_id, count(*) AS cnt,
       count(DISTINCT o.bookmaker) AS bm_cnt,
       max(o.snapshot_time) AS latest,
       min(o.snapshot_time) AS earliest,
       count(*) FILTER (WHERE o.is_opening) AS opening_cnt,
       count(*) FILTER (WHERE o.over_odds IS NOT NULL) AS ou_cnt
FROM odds_snapshots o
WHERE o.match_id = ANY($1::int[])
GROUP BY o.match_id
"""


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(SQL_MATCHES)
    print(f"=== 08-15 比赛周期（08-15 12:00 ~ 08-16 12:00）共 {len(rows)} 场 ===\n")

    match_ids = [r["id"] for r in rows]
    odds_rows = {}
    if match_ids:
        for r in await conn.fetch(SQL_ODDS, match_ids):
            odds_rows[r["match_id"]] = r

    problems = []
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
        # 原始竞彩队名 vs 映射中文名（去空格、去"FC"等噪音后模糊判断）
        h_name = (r["hzh"] or "").replace(" ", "")
        a_name = (r["azh"] or "").replace(" ", "")
        if r["hraw"] and h_name and not (r["hraw"] in h_name or h_name in r["hraw"]):
            flags.append(f"主名疑似不符(raw={r['hraw']})")
        if r["araw"] and a_name and not (r["araw"] in a_name or a_name in r["araw"]):
            flags.append(f"客名疑似不符(raw={r['araw']})")

        o = odds_rows.get(r["id"])
        if o is None or o["cnt"] == 0:
            flags.append("无赔率快照")
        elif o["ou_cnt"] == 0:
            flags.append("无大小球赔率")

        line = (f"  #{r['id']} [{r['lg']}] {r['match_num']} "
                f"{r['hzh'] or r['hen']}(id={r['hid']},sm={r['hsm']}) "
                f"vs {r['azh'] or r['aen']}(id={r['aid']},sm={r['asm']}) "
                f"ko={r['kickoff_time']} fx={r['fx']} status={r['status']}")
        if o:
            line += (f" | 赔率:{o['cnt']}条/{o['bm_cnt']}家 开盘{bool(o['opening_cnt'])} "
                     f"最新={o['latest']} 大小球{bool(o['ou_cnt'])}")
        if flags:
            line += f"  <<< {','.join(flags)}"
            problems.append((r, flags, o))
        print(line)

    print(f"\n=== 问题汇总：{len(problems)} 场存在潜在问题 ===")
    await conn.close()


asyncio.run(main())
