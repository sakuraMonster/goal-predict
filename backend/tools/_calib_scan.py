"""沙职/法乙 Model C 校准系数扫描（生产判定口径：actual in snap_top2，不 cap）
对历史样本逐场乘系数 k，计算命中率随 k 的变化，评估线性校准是否有效。
"""
import asyncio
import asyncpg
import sys

sys.path.insert(0, "app")
from app.predictor.snap import snap_top2

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def fetch(lg_zh):
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, th.name_zh AS hzh, ta.name_zh AS azh,
               p.expected_goals_c, p.actual_total_goals
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        JOIN predictions p ON p.match_id = m.id
        WHERE l.name_zh = $1 AND p.actual_total_goals IS NOT NULL
        ORDER BY m.kickoff_time
        """,
        lg_zh,
    )
    await conn.close()
    return rows


def scan(rows):
    """k 从 0.55 到 2.00 扫描，返回每 k 的命中明细"""
    results = {}
    for k10 in range(55, 205, 5):
        k = k10 / 100
        hits = []
        for r in rows:
            lam = (r["expected_goals_c"] or 0) * k
            top2 = snap_top2(lam)
            hit = r["actual_total_goals"] in top2
            hits.append((r, top2, hit))
        results[k] = hits
    return results


def report(lg_zh, rows):
    n = len(rows)
    base = 0
    base_hits = []
    for r in rows:
        top2 = snap_top2(r["expected_goals_c"])
        hit = r["actual_total_goals"] in top2
        base += hit
        base_hits.append((r, top2, hit))
    print(f"\n{'='*90}")
    print(f"  {lg_zh} · 样本={n} · 基线命中(×1.0)={base}/{n}={base/n*100:.0f}%")
    print(f"{'='*90}")
    print(f"  {'k':>5} {'命中':>4} {'命中率':>6}   明细(对阵→top2,命中)")
    results = scan(rows)
    for k in sorted(results):
        hits = results[k]
        cnt = sum(1 for _, _, h in hits if h)
        if k in (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.25, 1.3, 1.4, 1.5, 1.6, 1.8, 2.0):
            detail = "  ".join(
                f"{r['hzh'][:4]}v{r['azh'][:4]}({r['actual_total_goals']}球){t2}{'✓' if h else '✗'}"
                for r, t2, h in hits
            )
            print(f"  {k:>5.2f} {cnt:>3}/{n:<3} {cnt/n*100:>5.0f}%   {detail}")
    print(f"\n  基线明细: " + "  ".join(f"{r['hzh'][:4]}v{r['azh'][:4]}({r['actual_total_goals']}球){t2}{'✓' if h else '✗'}" for r, t2, h in base_hits))


async def main():
    for lg in ("沙职", "法乙"):
        rows = await fetch(lg)
        report(lg, rows)


asyncio.run(main())
