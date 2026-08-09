"""拉取葡超/荷乙/德乙/英冠上赛季数据，计算历史 calib"""
import asyncio
import asyncpg

LEAGUES = ["葡超", "荷乙", "德乙", "英冠"]

async def main():
    c = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    # 1. 先看数据范围：每个联赛的比赛时间分布
    print("=== 各联赛数据时间范围 ===")
    for lg in LEAGUES:
        rows = await c.fetch("""
            SELECT MIN(m.kickoff_time) as min_ko, MAX(m.kickoff_time) as max_ko,
                   COUNT(*) as total,
                   COUNT(CASE WHEN p.id IS NOT NULL THEN 1 END) as with_pred
            FROM matches m
            LEFT JOIN predictions p ON p.match_id = m.id
            WHERE m.league_id = (SELECT id FROM leagues WHERE name_zh = $1)
        """, lg)
        r = rows[0]
        print(f"  {lg}: {r['total']}场比赛, {r['with_pred']}有预测, 时间 {r['min_ko']} ~ {r['max_ko']}")

    print()

    # 2. 按年度分组查看（假设赛季跨年，以年为单位）
    print("=== 按年度分组 ===")
    for lg in LEAGUES:
        rows = await c.fetch("""
            SELECT EXTRACT(YEAR FROM m.kickoff_time)::int as yr, COUNT(*) as cnt,
                   COUNT(CASE WHEN p.actual_total_goals IS NOT NULL THEN 1 END) as settled
            FROM matches m
            LEFT JOIN predictions p ON p.match_id = m.id
            WHERE m.league_id = (SELECT id FROM leagues WHERE name_zh = $1)
            GROUP BY yr
            ORDER BY yr DESC
        """, lg)
        print(f"  {lg}:")
        for r in rows:
            print(f"    {r['yr']}: {r['cnt']}场, 已结算{r['settled']}场")

    print()

    # 3. 对每个联赛，取有预测且已结算的数据，计算历史实际进球率
    # 按赛季分段（假设 2025 年及以前的为上一赛季）
    for lg in LEAGUES:
        rows = await c.fetch("""
            SELECT p.kickoff_time, p.expected_goals, p.expected_goals_c,
                   p.actual_total_goals, p.actual_score,
                   m.home_team_name, m.away_team_name, m.match_num,
                   m.goal_line
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE m.league_id = (SELECT id FROM leagues WHERE name_zh = $1)
              AND p.actual_total_goals IS NOT NULL
            ORDER BY p.kickoff_time
        """, lg)

        if not rows:
            print(f"\n=== {lg}: 无已结算预测数据 ===")
            continue

        total = len(rows)
        goals_sum = sum(r["actual_total_goals"] for r in rows)
        avg_goals = round(goals_sum / total, 2)
        eg_sum = sum(r["expected_goals"] or 0 for r in rows)
        avg_eg = round(eg_sum / total, 2) if total else 0
        eg_c_sum = sum(r["expected_goals_c"] or 0 for r in rows)
        avg_eg_c = round(eg_c_sum / total, 2) if total else 0

        print(f"\n=== {lg}: {total}场已结算 ===")
        print(f"  实际场均进球: {avg_goals}")
        print(f"  Model B γ均值: {avg_eg}")
        print(f"  Model C γ均值: {avg_eg_c}")

        # 如果有 goal_line 数据
        gl_rows = [r for r in rows if r["goal_line"] is not None]
        if gl_rows:
            gl_avg = sum(r["goal_line"] for r in gl_rows) / len(gl_rows)
            # actual/goal_line 比
            actual_gl_ratio = sum(r["actual_total_goals"] for r in gl_rows) / sum(r["goal_line"] for r in gl_rows)
            print(f"  goal_line 均值: {gl_avg:.2f} (有数据的{len(gl_rows)}场)")
            print(f"  actual/goal_line 比: {actual_gl_ratio:.4f}")
            print(f"  建议 calib: {actual_gl_ratio:.4f}")
        else:
            print(f"  goal_line: 无数据")

        # 打印每场详情（如果场次不多）
        if total <= 30:
            print(f"  逐场:")
            for r in rows:
                gl = f"GL={r['goal_line']}" if r["goal_line"] else ""
                print(f"    {str(r['kickoff_time'])[:10]} {r['home_team_name']} vs {r['away_team_name']}  {r['actual_score']} (总{r['actual_total_goals']})  Bγ={r['expected_goals']:.1f}  Cγ={r['expected_goals_c']:.1f}  {gl}")

    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
