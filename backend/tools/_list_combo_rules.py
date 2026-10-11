"""列出近1个月符合「组合规则」的比赛（只读）：命中规则 / 选中项 / 该选项赔率 / 赛果 / 命中。

规则（来自 _bt_odds_combo_scan.py 的稳定组合）：
  R1  让方不胜 : 让方胜赔>=2.5 且 平赔<3.0      -> 双选 让平+让负
  R1b 让方不胜 : 让方胜赔>=2.5 且 让球线=-1 且 主队让球 -> 双选 让平+让负
  R2  客队不败 : 让球方=客队 且 平赔>=4.0 且 受让方(主队)赔率>=5.5 -> 双选 平+客
  R3  无平     : 让球线=-2（主让2球）           -> 双选 主+客
  R4  主胜     : 让球线=-2（主让2球）           -> 单选 主胜
  R5  平局     : 平赔∈[2.5,3.0) 且 受让方赔率∈[1.8,3.0) -> 单选 平
运行：docker exec -w /app -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/combo_list.py
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

RULES = ["R1 让方不胜(胜赔≥2.5&平<3.0)", "R1b 让方不胜(≥2.5&线-1&主让)",
         "R2 客队不败(客让&平≥4&主赔≥5.5)", "R3 无平(主让2球)",
         "R4 主胜(主让2球)", "R5 平局(平2.5-3.0&受让1.8-3.0)"]


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()
    last = {}
    for r in rows:
        last[r["st"]] = r  # 先按时间
    # 每场取最后一条快照
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
        adj = hs + line
        if fav == "home":
            cover = adj > as_; push = adj == as_
        else:
            cover = adj < as_; push = adj == as_
        res_fav = "让胜" if cover else ("让平" if push else "让负")
        res_had = "主" if hs > as_ else ("平" if hs == as_ else "客")

        def add(rule, pick, odds_s, res, hit):
            out.append((r["d"], r["league"] or "国际赛事", f"{r['home']} vs {r['away']}",
                        rule, pick, odds_s, res, hit))

        if fav_odds >= 2.5 and hd < 3.0:
            add(RULES[0], "让平+让负", f"让平{hd:.2f}/让负{loss_odds:.2f}", res_fav, res_fav in ("让平", "让负"))
        if fav_odds >= 2.5 and line == -1 and fav == "home":
            add(RULES[1], "让平+让负", f"让平{hd:.2f}/让负{loss_odds:.2f}", res_fav, res_fav in ("让平", "让负"))
        if fav == "away" and hd >= 4.0 and hh >= 5.5:
            add(RULES[2], "平+客", f"平{hd:.2f}/客{ha:.2f}", res_had, res_had in ("平", "客"))
        if line == -2:
            add(RULES[3], "主+客", f"主{hh:.2f}/客{ha:.2f}", res_had, res_had in ("主", "客"))
            add(RULES[4], "主胜", f"主{hh:.2f}", res_had, res_had == "主")
        if 2.5 <= hd < 3.0 and 1.8 <= dog_odds < 3.0:
            add(RULES[5], "平", f"平{hd:.2f}", res_had, res_had == "平")

    out.sort(key=lambda x: (x[0], x[3]), reverse=True)
    print(f"近1个月符合组合规则的场次：共 {len(out)} 条\n")
    print("| 日期 | 联赛 | 对阵 | 命中规则 | 选中 | 选项赔率 | 实际 | 命中 |")
    print("|---|---|---|---|---|---|---|---|")
    for d, lg, vs, rule, pick, od, res, hit in out:
        print(f"| {d} | {lg} | {vs} | {rule} | {pick} | {od} | {res} | {'✓' if hit else '✗'} |")

    print("\n按规则汇总：")
    print("| 规则 | n | 命中 | 命中率 |")
    print("|---|---|---|---|")
    for rule in RULES:
        seg = [x for x in out if x[3] == rule]
        if not seg:
            print(f"| {rule} | 0 | 0 | - |")
            continue
        h = sum(1 for x in seg if x[7])
        print(f"| {rule} | {len(seg)} | {h} | {h/len(seg)*100:.0f}% |")


asyncio.run(main())
