"""沙职/法乙 Model C 历史偏差核算（定校准系数用）"""
import asyncio
import asyncpg
import json
from collections import defaultdict

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 联赛表：沙职/法乙 ===")
    rows = await conn.fetch(
        "SELECT id, name_zh, name_en FROM leagues WHERE name_zh IN ('沙职', '法乙') OR name_en ILIKE '%pro league%' OR name_en ILIKE '%ligue 2%'"
    )
    for r in rows:
        print(f"  id={r['id']} {r['name_zh']} {r['name_en']}")

    # 沙职/法乙历史 Model C 偏差
    for lg_zh in ("沙职", "法乙"):
        print(f"\n=== {lg_zh} Model C 历史偏差（λ/实）===")
        rows = await conn.fetch(
            """
            SELECT m.id, m.match_num, m.kickoff_time,
                   th.name_zh AS hzh, ta.name_zh AS azh,
                   p.expected_goals_c, p.snap_top2_c, p.actual_total_goals
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
        if not rows:
            print("  无数据")
            continue
        ratios = []
        print(f"  {'match':<6}{'时间':<12}{'对阵':<24}{'γC':>5} {'实际':>4} {'λ/实':>6} {'偏差':>7}")
        for r in rows:
            act = r["actual_total_goals"]
            gc = r["expected_goals_c"]
            ratio = gc / act if act else None
            dev = gc - act
            if ratio is not None:
                ratios.append(ratio)
            print(f"  {r['id']:<6}{str(r['kickoff_time'])[:10]:<12} {(r['hzh'] or '')[:8]}vs{(r['azh'] or '')[:8]:<16} {gc:>5.2f} {act:>4} {ratio:>6.2f} {dev:>+7.2f}")
        n = len(ratios)
        if n:
            avg = sum(ratios) / n
            # 按实际进球分组统计（看分布）
            by_act = defaultdict(list)
            for r in rows:
                by_act[r["actual_total_goals"]].append(r["expected_goals_c"])
            print(f"\n  样本={n}  λ/实 均值={avg:.3f}")
            print(f"  建议系数(1/ratio均值)={1/avg:.3f}   保守(1/ratio中位)={1/sorted(ratios)[n//2]:.3f}")
            print(f"  按实际进球分组:")
            for a in sorted(by_act):
                gcs = by_act[a]
                print(f"    实际={a}球: {len(gcs)}场 γC均值={sum(gcs)/len(gcs):.2f}")

    await conn.close()


asyncio.run(main())
