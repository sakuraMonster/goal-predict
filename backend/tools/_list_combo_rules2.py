"""列出近1个月符合「≥70% 候选组合规则」的比赛（只读），含赛果。

候选规则（逐场要求选中项赔率>=1.5）：
  R1 让方不胜: 让方胜赔∈[1.8,2.1) 且 平赔∈[2.5,3.0)            -> 让平+让负
  R2 让方不胜: 平赔∈[2.5,3.0) 且 让球线=-1(主让)                -> 让平+让负
  R3 让满    : 让方胜赔<1.4 且 受让方赔率>=5.5 且 让球线=+1(客让) -> 让胜+让平
  R4 主队不败: 让方胜赔∈[1.4,1.6) 且 平赔∈[3.6,4.0) 且 线=-1      -> 主+平
  R5 客队不败: 让方胜赔∈[1.4,1.6) 且 线=+1                      -> 平+客
  R6 分出胜负: 平赔∈[3.6,4.0) 且 受让方赔率∈[1.8,3.0)            -> 主+客
运行：docker exec -w /app -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/combo_list2.py
"""
import os
import asyncio
import asyncpg

SQL = """
SELECT m.kickoff_time::date::text AS d,
       l.name_zh AS league,
       COALESCE(ht.name_zh, m.home_team_name) AS home,
       COALESCE(at.name_zh, m.away_team_name) AS away,
       m.home_score, m.away_score,
       s.had_home, s.had_draw, s.had_away,
       s.hhad_line, s.hhad_home, s.hhad_draw, s.hhad_away, s.snapshot_time::text AS st
FROM matches m
JOIN jczq_play_odds_snapshots s ON s.match_id = m.id
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN teams ht ON ht.id = m.home_team_id
LEFT JOIN teams at ON at.id = m.away_team_id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND m.kickoff_time::date >= (CURRENT_DATE - INTERVAL '31 days')
  AND s.had_home IS NOT NULL AND s.had_draw IS NOT NULL AND s.had_away IS NOT NULL
ORDER BY m.kickoff_time, m.id, s.snapshot_time
"""

RULES = ["R1 让方不胜(胜赔1.8-2.1&平2.5-3.0)", "R2 让方不胜(平2.5-3.0&线-1)",
         "R3 让满(胜赔<1.4&受让≥5.5&客让)", "R4 主队不败(胜赔1.4-1.6&平3.6-4.0&主让)",
         "R5 客队不败(胜赔1.4-1.6&客让)", "R6 分出胜负(平3.6-4.0&受让1.8-3.0)"]
MIN_LEG = 1.5


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()
    per_match = {}
    for r in rows:
        per_match[(r["d"], r["home"], r["away"])] = r

    out = []
    for r in per_match.values():
        hs, as_ = r["home_score"], r["away_score"]
        hh, hd, ha = r["had_home"], r["had_draw"], r["had_away"]
        line = float(r["hhad_line"])
        fav = "home" if line < 0 else "away"
        fav_odds = hh if fav == "home" else ha
        dog_odds = ha if fav == "home" else hh
        loss_odds = r["hhad_away"] if fav == "home" else r["hhad_home"]
        cover_odds = r["hhad_home"] if fav == "home" else r["hhad_away"]
        adj = hs + line
        if fav == "home":
            cover = adj > as_; push = adj == as_
        else:
            cover = adj < as_; push = adj == as_
        res_fav = "让胜" if cover else ("让平" if push else "让负")
        res_had = "主" if hs > as_ else ("平" if hs == as_ else "客")
        score = f"{hs}-{as_}"

        def add(rule, pick, o1, o2, res, hit):
            legs = [o for o in (o1, o2) if o is not None]
            if min(legs) < MIN_LEG:   # 逐场：所有选中项赔率必须 >=1.5
                return
            od = "/".join(f"{o:.2f}" for o in legs)
            out.append((r["d"], r["league"] or "国际赛事", f"{r['home']} vs {r['away']}",
                        rule, pick, od, score, res, hit))

        if 1.8 <= fav_odds < 2.1 and 2.5 <= hd < 3.0:
            add(RULES[0], "让平+让负", hd, loss_odds, res_fav, res_fav in ("让平", "让负"))
        if 2.5 <= hd < 3.0 and line == -1:
            add(RULES[1], "让平+让负", hd, loss_odds, res_fav, res_fav in ("让平", "让负"))
        if fav_odds < 1.4 and dog_odds >= 5.5 and line == 1:
            add(RULES[2], "让胜+让平", cover_odds, hd, res_fav, res_fav in ("让胜", "让平"))
        if 1.4 <= fav_odds < 1.6 and 3.6 <= hd < 4.0 and line == -1:
            add(RULES[3], "主+平", hh, hd, res_had, res_had in ("主", "平"))
        if 1.4 <= fav_odds < 1.6 and line == 1:
            add(RULES[4], "平+客", hd, ha, res_had, res_had in ("平", "客"))
        if 3.6 <= hd < 4.0 and 1.8 <= dog_odds < 3.0:
            add(RULES[5], "主+客", hh, ha, res_had, res_had in ("主", "客"))

    out.sort(key=lambda x: (x[0], x[3]), reverse=True)
    print(f"近1个月符合候选规则的场次：共 {len(out)} 条（逐场要求选中项赔率均>=1.5）\n")
    print("| 日期 | 联赛 | 对阵 | 命中规则 | 选中 | 选项赔率 | 比分 | 赛果 | 命中 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for d, lg, vs, rule, pick, od, score, res, hit in out:
        print(f"| {d} | {lg} | {vs} | {rule} | {pick} | {od} | {score} | {res} | {'✓' if hit else '✗'} |")
    print("\n按规则汇总：")
    print("| 规则 | n | 命中 | 命中率 |")
    print("|---|---|---|---|")
    for rule in RULES:
        seg = [x for x in out if x[3] == rule]
        h = sum(1 for x in seg if x[8])
        print(f"| {rule} | {len(seg)} | {h} | {h/len(seg)*100:.0f}% |" if seg else f"| {rule} | 0 | 0 | - |")


asyncio.run(main())
