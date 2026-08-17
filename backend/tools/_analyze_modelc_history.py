"""历史 Model C 进球数命中率分析（SQL 端判断命中，避免 asyncpg JSON 解析问题）"""
import asyncio
import asyncpg
from datetime import datetime
from collections import defaultdict

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    # 口径：snap_top2_c 命中；act_capped = LEAST(actual_total_goals, 4)
    # jsonb 包含判断：snap_top2_c @> to_jsonb(LEAST(...))
    rows = await conn.fetch(
        """
        SELECT p.expected_goals_c AS egc,
               p.snap_top2_c::text AS snap_c,
               p.actual_total_goals AS act,
               l.name_zh AS lg,
               (p.snap_top2_c @> to_jsonb(LEAST(p.actual_total_goals, 4)::int)) AS hit
        FROM predictions p
        LEFT JOIN leagues l ON l.id = p.league_id
        WHERE p.actual_total_goals IS NOT NULL
          AND p.snap_top2_c IS NOT NULL
          AND jsonb_array_length(p.snap_top2_c) = 2
          AND p.kickoff_time >= $1
        """,
        datetime(2026, 7, 15, 12, 0),
    )

    by_league = defaultdict(lambda: {"n": 0, "hit": 0})
    by_lambda = defaultdict(lambda: {"n": 0, "hit": 0})
    by_snap0 = defaultdict(lambda: {"n": 0, "hit": 0})
    total_n = 0
    total_hit = 0

    for r in rows:
        hit = bool(r["hit"])
        lg = r["lg"] or "未知"
        egc = r["egc"] or 0
        snap0 = int(r["snap_c"].strip("[]").split(",")[0].strip())

        by_league[lg]["n"] += 1
        by_league[lg]["hit"] += int(hit)

        if egc < 2.0:
            bucket = "<2.0"
        elif egc < 2.5:
            bucket = "2.0-2.5"
        elif egc < 3.0:
            bucket = "2.5-3.0"
        elif egc < 3.5:
            bucket = "3.0-3.5"
        else:
            bucket = ">=3.5"
        by_lambda[bucket]["n"] += 1
        by_lambda[bucket]["hit"] += int(hit)

        by_snap0[snap0]["n"] += 1
        by_snap0[snap0]["hit"] += int(hit)

        total_n += 1
        total_hit += int(hit)

    print(f"=== 近30天 Model C 进球数命中（SNAP Top2, cap4）===")
    print(f"整体: {total_hit}/{total_n} = {total_hit/total_n*100:.1f}%\n")

    print("--- 按联赛（n>=5）---")
    for lg, s in sorted(by_league.items(), key=lambda x: -x[1]["n"]):
        if s["n"] >= 5:
            print(f"  {lg}: {s['hit']}/{s['n']} = {s['hit']/s['n']*100:.1f}%")

    print("\n--- 按 λ_c 区间 ---")
    for b in ["<2.0", "2.0-2.5", "2.5-3.0", "3.0-3.5", ">=3.5"]:
        s = by_lambda[b]
        acc = s["hit"]/s["n"]*100 if s["n"] else 0
        print(f"  λ_c {b}: {s['hit']}/{s['n']} = {acc:.1f}%")

    print("\n--- 按 SNAP 首位值 ---")
    for s0 in sorted(by_snap0):
        s = by_snap0[s0]
        acc = s["hit"]/s["n"]*100 if s["n"] else 0
        print(f"  snap首位={s0}: {s['hit']}/{s['n']} = {acc:.1f}%")

    await conn.close()


asyncio.run(main())
