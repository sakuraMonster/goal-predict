"""诊断：核查「客队让球」(hhad_line>0) 的处理是否正确（只读）。

验证不变式（用全部已结算样本）：
  主队让球(line<0)：HHAD 主胜项(hhad_home) 应 <= HHAD 客胜项(hhad_away)；HAD 主胜赔应 <= 客胜赔
  客队让球(line>0)：HHAD 客胜项(hhad_away) 应 <= HHAD 主胜项(hhad_home)；HAD 客胜赔应 <= 主胜赔
若大量违反 -> 符号/标签约定与代码假设不一致。
并打印若干「客队让球」原始行，便于人工核对 让胜/让平/让负。
运行：docker exec -w /app -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/diag_hcp.py
"""
import os
import asyncio
import asyncpg

SQL = """
SELECT m.id AS match_id, m.kickoff_time::date::text AS d,
       COALESCE(ht.name_zh, m.home_team_name) AS home,
       COALESCE(at.name_zh, m.away_team_name) AS away,
       m.home_score, m.away_score,
       s.had_home, s.had_draw, s.had_away,
       s.hhad_line, s.hhad_home, s.hhad_draw, s.hhad_away, s.snapshot_time::text AS st
FROM matches m
JOIN jczq_play_odds_snapshots s ON s.match_id = m.id
LEFT JOIN teams ht ON ht.id = m.home_team_id
LEFT JOIN teams at ON at.id = m.away_team_id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND s.had_home IS NOT NULL AND s.hhad_line IS NOT NULL
ORDER BY m.id, s.snapshot_time
"""


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()
    per = {}
    for r in rows:
        per[r["match_id"]] = r  # 最后一条快照
    rs = list(per.values())

    home_give = [r for r in rs if r["hhad_line"] < 0]
    away_give = [r for r in rs if r["hhad_line"] > 0]
    print(f"总场次={len(rs)}  主队让球={len(home_give)}  客队让球={len(away_give)}")

    def viol(seg, name, cond):
        bad = [r for r in seg if not cond(r)]
        print(f"  {name}: 违反 {len(bad)}/{len(seg)}")
        return bad

    print("\n[不变式] 让球方在 HHAD 的覆盖项应更便宜：")
    viol(home_give, "主让: hhad_home<=hhad_away", lambda r: r["hhad_home"] <= r["hhad_away"])
    viol(away_give, "客让: hhad_away<=hhad_home", lambda r: r["hhad_away"] <= r["hhad_home"])
    print("\n[不变式] 让球方在 HAD 的独赢赔率应更低：")
    viol(home_give, "主让: had_home<=had_away", lambda r: r["had_home"] <= r["had_away"])
    viol(away_give, "客让: had_away<=had_home", lambda r: r["had_away"] <= r["had_home"])

    print("\n[样本] 随机 15 场客队让球（原始值）：")
    print(f"  {'日期':<11}{'对阵':<26}{'线':>4}{'HAD主/平/客':>18}{'HHAD主/平/客':>18}{'比分':>7}  让球结果(fav视角)")
    for r in away_give[:15]:
        hs, as_ = r["home_score"], r["away_score"]
        line = float(r["hhad_line"])
        adj = hs + line
        res_fav = "让胜" if adj < as_ else ("让平" if adj == as_ else "让负")  # fav=away
        vs = f"{(r['home'] or '?')[:11]} vs {(r['away'] or '?')[:11]}"
        print(f"  {r['d']:<11}{vs:<26}{line:>+4.0f}"
              f"  {r['had_home']:>5.2f}/{r['had_draw']:>4.2f}/{r['had_away']:>4.2f}"
              f"  {r['hhad_home']:>5.2f}/{r['hhad_draw']:>4.2f}/{r['hhad_away']:>4.2f}"
              f"  {hs}-{as_:<4}  {res_fav}")


asyncio.run(main())
