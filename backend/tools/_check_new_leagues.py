"""分析新联赛（葡超/荷乙/德乙/英冠）的实际进球特征"""
import asyncio
import asyncpg
import json

async def main():
    c = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    for lg_name in ["葡超", "荷乙", "德乙", "英冠"]:
        rows = await c.fetch("""
            SELECT p.id, p.expected_goals_c, p.snap_top2_c,
                   p.actual_total_goals, p.actual_score,
                   m.home_team_name, m.away_team_name, m.match_num,
                   p.kickoff_time
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE m.league_id = (SELECT id FROM leagues WHERE name_zh = $1)
              AND p.actual_total_goals IS NOT NULL
            ORDER BY p.kickoff_time
        """, lg_name)

        if not rows:
            print(f"=== {lg_name}: 无已结算数据 ===")
            print()
            continue

        total = len(rows)
        goals_sum = sum(r["actual_total_goals"] for r in rows)
        avg_goals = round(goals_sum / total, 2)
        eg_sum = sum(r["expected_goals_c"] or 0 for r in rows)
        avg_eg = round(eg_sum / total, 2)

        # 命中率
        hit = 0
        for r in rows:
            snap = json.loads(r["snap_top2_c"]) if isinstance(r["snap_top2_c"], str) else (r["snap_top2_c"] or [])
            act = r["actual_total_goals"]
            if act is not None and snap:
                if min(act, 4) in snap:
                    hit += 1

        print(f"=== {lg_name}: {total}场已结算 ===")
        print(f"  实际场均进球: {avg_goals}  预测场均γ: {avg_eg}  偏差: {avg_eg - avg_goals:+.2f}")
        print(f"  进球命中率: {hit}/{total} = {round(hit/total*100,1)}%")
        print()

        for r in rows:
            snap = json.loads(r["snap_top2_c"]) if isinstance(r["snap_top2_c"], str) else (r["snap_top2_c"] or [])
            snap_str = "/".join(str(x) for x in snap[:2]) if snap else "?"
            act = r["actual_total_goals"]
            act_cap = min(act, 4) if act is not None else None
            h = "✓" if act_cap is not None and act_cap in snap else "✗"
            print(f"  {r['match_num']}  {r['home_team_name']} vs {r['away_team_name']}  {r['actual_score']} (总{act})  γ={r['expected_goals_c']:.1f}  SNAP={snap_str}  {h}")
        print()

    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
