"""检查08-07比赛日所有比赛及预测情况 —— 覆盖前后多个周期"""
import asyncio
import asyncpg
from datetime import datetime

async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    # 检查 08-06、08-07、08-08 三个周期的比赛
    cycles = [
        ("08-06", datetime(2026, 8, 5, 12, 0), datetime(2026, 8, 6, 12, 0)),
        ("08-07", datetime(2026, 8, 6, 12, 0), datetime(2026, 8, 7, 12, 0)),
        ("08-08", datetime(2026, 8, 7, 12, 0), datetime(2026, 8, 8, 12, 0)),
    ]

    for label, start, end in cycles:
        rows = await conn.fetch("""
            SELECT m.id, m.home_team_name, m.away_team_name, m.kickoff_time,
                   m.match_num, l.name_zh AS league,
                   CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END AS has_pred
            FROM matches m
            LEFT JOIN predictions p ON p.match_id = m.id
            JOIN leagues l ON m.league_id = l.id
            WHERE m.kickoff_time >= $1
              AND m.kickoff_time < $2
            ORDER BY m.kickoff_time
        """, start, end)

        with_pred = sum(1 for r in rows if r["has_pred"])
        print(f"=== {label} cycle ({start} ~ {end}): {len(rows)} matches, {with_pred} with predictions ===")
        for r in rows:
            mark = "P" if r["has_pred"] else " "
            print(f"  [{mark}] {r['league']} {r['home_team_name']} vs {r['away_team_name']}  {r['kickoff_time']}  #{r['match_num']}")
        print()

    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
