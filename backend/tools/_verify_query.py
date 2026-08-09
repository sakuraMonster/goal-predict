"""对比参数化查询 vs 原始 SQL"""
import asyncio
import asyncpg
from datetime import datetime

async def main():
    c = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    # 1. 用户原始 SQL
    print("=== 用户SQL: select * from matches ===")
    rows = await c.fetch(
        "select * from matches where kickoff_time > '2026-08-07 12:00:00' and kickoff_time < '2026-08-08 12:00:00' order by kickoff_time desc"
    )
    print(f"matches: {len(rows)} 条")
    for r in rows:
        print(f"  id={r['id']}  {r['home_team_name']} vs {r['away_team_name']}  ko={r['kickoff_time']}")

    print()

    print("=== 用户SQL: select * from predictions ===")
    rows2 = await c.fetch(
        "select kickoff_time, id, match_id from predictions where kickoff_time > '2026-08-07 12:00:00' and kickoff_time < '2026-08-08 12:00:00' order by kickoff_time desc"
    )
    print(f"predictions: {len(rows2)} 条")
    for r in rows2:
        print(f"  pred_id={r['id']}  match_id={r['match_id']}  ko={r['kickoff_time']}")

    print()

    # 2. 我之前的 JOIN 查询（参数化）
    print("=== 参数化 JOIN 查询 ===")
    start = datetime(2026, 8, 7, 12, 0)
    end = datetime(2026, 8, 8, 12, 0)
    rows3 = await c.fetch("""
        SELECT p.id, p.kickoff_time, m.home_team_name, m.away_team_name, l.name_zh AS lg
        FROM predictions p
        JOIN matches m ON p.match_id = m.id
        JOIN leagues l ON m.league_id = l.id
        WHERE p.kickoff_time >= $1 AND p.kickoff_time < $2
        ORDER BY p.kickoff_time DESC
    """, start, end)
    print(f"JOIN: {len(rows3)} 条")
    for r in rows3:
        print(f"  pred_id={r['id']}  {r['home_team_name']} vs {r['away_team_name']}  ko={r['kickoff_time']}")

    print()

    # 3. 参数化但不用 JOIN，只用 predictions
    print("=== 参数化 predictions (无JOIN) ===")
    rows4 = await c.fetch("""
        SELECT id, kickoff_time, match_id FROM predictions
        WHERE kickoff_time >= $1 AND kickoff_time < $2
        ORDER BY kickoff_time DESC
    """, start, end)
    print(f"predictions (params): {len(rows4)} 条")
    for r in rows4:
        print(f"  pred_id={r['id']}  match_id={r['match_id']}  ko={r['kickoff_time']}")

    print()

    # 4. 检查 datetime 参数值
    print(f"start={start}  end={end}")
    print(f"start repr={repr(start)}  end repr={repr(end)}")

    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
