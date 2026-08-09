"""与后端 API 完全一致的查询逻辑验证"""
import asyncio
import asyncpg
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))

def date_range(date_str):
    """完全复制后端 _date_range 逻辑"""
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BEIJING)
    start = d.replace(hour=12, minute=0, second=0, microsecond=0)
    end = (d + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)

async def main():
    c = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    for date_str in ["2026-08-06", "2026-08-07", "2026-08-08"]:
        start, end = date_range(date_str)
        rows = await c.fetch("""
            SELECT m.kickoff_time, m.match_num, m.home_team_name, m.away_team_name,
                   l.name_zh AS lg, CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END AS pred
            FROM matches m
            LEFT JOIN predictions p ON p.match_id = m.id
            JOIN leagues l ON m.league_id = l.id
            WHERE m.kickoff_time >= $1 AND m.kickoff_time < $2
            ORDER BY m.kickoff_time
        """, start, end)
        print(f"=== date={date_str}  range={start} ~ {end}  ({len(rows)} matches) ===")
        for r in rows:
            print(f"  {r['match_num']:8s} {r['lg']:6s} {r['home_team_name']} vs {r['away_team_name']}  ko={r['kickoff_time']}  pred={'Y' if r['pred'] else 'N'}")

    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
