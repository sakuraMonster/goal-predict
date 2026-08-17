"""确认 08-14 周期 016 场次 + 完整 17 场清单"""
import asyncio
import asyncpg
from datetime import datetime

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.kickoff_time, m.status, l.name_zh AS lg,
               th.name_zh AS hzh, ta.name_zh AS azh
        FROM matches m
        JOIN teams th ON th.id=m.home_team_id
        JOIN teams ta ON ta.id=m.away_team_id
        LEFT JOIN leagues l ON l.id=m.league_id
        WHERE m.kickoff_time >= $1 AND m.kickoff_time < $2
        ORDER BY m.match_num
        """,
        datetime(2026, 8, 14, 12, 0),
        datetime(2026, 8, 15, 12, 0),
    )
    print(f"共 {len(rows)} 场：")
    for r in rows:
        print(f"  id={r['id']} num={r['match_num']} ko={r['kickoff_time']} st={r['status']} [{r['lg']}] {r['hzh']} vs {r['azh']}")

    print("\n=== 016 单独查 ===")
    r16 = await conn.fetch("SELECT id, match_num, kickoff_time, status FROM matches WHERE match_num ILIKE '%016%' ORDER BY kickoff_time DESC LIMIT 10")
    for r in r16:
        print(f"  id={r['id']} num={r['match_num']} ko={r['kickoff_time']} st={r['status']}")
    await conn.close()


asyncio.run(main())
