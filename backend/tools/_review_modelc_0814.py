"""08-14 周期 Model C 专项复盘（仅 Model C，其余忽略）
口径：snap_top2_c 判进球，实际总进球 cap 4；偏差 = γC - 实际
"""
import asyncio
import asyncpg
import json
from collections import defaultdict

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

SQL = """
SELECT p.match_id, m.match_num, l.name_zh AS lg,
       th.name_zh AS hzh, ta.name_zh AS azh,
       p.expected_goals_c, p.snap_top2_c, p.actual_total_goals, p.is_cold_match,
       p.confidence_level
FROM predictions p
JOIN matches m ON m.id = p.match_id
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN teams th ON th.id = m.home_team_id
LEFT JOIN teams ta ON ta.id = m.away_team_id
WHERE m.kickoff_time >= '2026-08-14 12:00:00' AND m.kickoff_time < '2026-08-15 12:00:00'
ORDER BY m.kickoff_time
"""


async def connect():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(SQL)
    await conn.close()
    return rows


def report(rows):
    hits = 0
    devs = []
    over = under = 0
    print("=" * 100)
    print("  Model C 专项复盘 · 08-14 周期（08-14 12:00 ~ 08-15 12:00）· 命中口径=snap_top2_c 含 cap4")
    print("=" * 100)
    print(f"  {'比赛':<16}{'联赛':<6}{'对阵':<24}{'γC':>5} {'SNAP_C':<10}{'实际':>4} {'命中':>4} {'偏差':>6}")
    for r in rows:
        act = r["actual_total_goals"]
        sc = json.loads(r["snap_top2_c"]) if isinstance(r["snap_top2_c"], str) else (r["snap_top2_c"] or [])
        cap = min(act, 4)
        hit = cap in sc
        hits += hit
        dev = (r["expected_goals_c"] or 0) - act
        devs.append(dev)
        if dev > 0.3:
            over += 1
        elif dev < -0.3:
            under += 1
        mark = "✓" if hit else "✗"
        teams = f"{r['hzh']} vs {r['azh']}"
        print(f"  {r['match_num']:<14} {r['lg']:<6} {teams[:22]:<24} {r['expected_goals_c']:>5.2f} {str(sc):<10} {act:>3} {mark:>4} {dev:>+6.2f}")

    n = len(rows)
    avg_dev = sum(devs) / n
    print("-" * 100)
    print(f"  命中: {hits}/{n} = {hits/n*100:.1f}%")
    print(f"  偏差: 平均={avg_dev:+.2f}  低估场次(dev<-0.3)={under}  高估场次(dev>+0.3)={over}")
    cold = sum(1 for r in rows if r["is_cold_match"])
    print(f"  冷门标记: {cold}/{n}")

    lg = defaultdict(list)
    for r in rows:
        lg[r["lg"] or "未知"].append(r)
    print("-" * 100)
    print("  按联赛（Model C 命中 / 平均偏差）:")
    for name, items in sorted(lg.items(), key=lambda x: -len(x[1])):
        h = 0
        ds = []
        for r in items:
            sc = json.loads(r["snap_top2_c"]) if isinstance(r["snap_top2_c"], str) else (r["snap_top2_c"] or [])
            h += min(r["actual_total_goals"], 4) in sc
            ds.append((r["expected_goals_c"] or 0) - r["actual_total_goals"])
        avg = sum(ds) / len(ds)
        print(f"    {name:<6} {len(items)}场  命中 {h}/{len(items)}={h/len(items)*100:.0f}%  平均偏差={avg:+.2f}")


if __name__ == "__main__":
    import sys
    out_path = "tools/_out_modelc_0814.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        old = sys.stdout
        sys.stdout = f
        try:
            report(asyncio.run(connect()))
        finally:
            sys.stdout = old
    print(f"已写入 {out_path}")
