"""检查数据库中的时间范围情况"""
import asyncio
import asyncpg
from datetime import datetime

async def main():
    c = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    # 宽范围：08-07 00:00 ~ 08-09 00:00
    rows = await c.fetch("""
        SELECT m.kickoff_time, m.match_num, m.home_team_name, m.away_team_name,
               l.name_zh AS lg, CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END AS has_pred
        FROM matches m
        LEFT JOIN predictions p ON p.match_id = m.id
        JOIN leagues l ON m.league_id = l.id
        WHERE m.kickoff_time >= $1 AND m.kickoff_time < $2
        ORDER BY m.kickoff_time
    """, datetime(2026, 8, 7), datetime(2026, 8, 9))

    print(f"Total matches in 08-07~08-08 (naive): {len(rows)}")
    for r in rows:
        print(f"  {r['match_num']}  {r['lg']}  {r['home_team_name']} vs {r['away_team_name']}  kickoff={r['kickoff_time']}  pred={'Y' if r['has_pred'] else 'N'}")

    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
