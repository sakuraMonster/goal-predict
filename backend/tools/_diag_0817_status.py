"""统计已完场（predictions.actual 非空）但 matches.status 仍为 scheduled 的影响面"""
import asyncio
import asyncpg
from datetime import datetime, timedelta, timezone

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
BJ = timezone(timedelta(hours=8))


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 全量：status=scheduled 但 prediction 已结算（actual 非空） ===")
    r = await conn.fetchrow(
        """SELECT count(*) AS n FROM matches m
           JOIN predictions p ON p.match_id=m.id
           WHERE m.status='scheduled' AND p.actual_home_score IS NOT NULL AND p.actual_away_score IS NOT NULL"""
    )
    print(f"  总数: {r['n']}")

    print("\n=== 按比赛日（12:00 口径）分布（近 10 天） ===")
    rows = await conn.fetch(
        """SELECT m.kickoff_time, m.id, m.status, m.home_score, m.away_score,
                  p.actual_home_score, p.actual_away_score
           FROM matches m JOIN predictions p ON p.match_id=m.id
           WHERE m.status='scheduled' AND p.actual_home_score IS NOT NULL AND p.actual_away_score IS NOT NULL
           ORDER BY m.kickoff_time DESC LIMIT 60"""
    )
    for x in rows:
        ms = "ms=NULL" if x["home_score"] is None else f"ms={x['home_score']}:{x['away_score']}"
        print(f"  #{x['id']} {x['kickoff_time']} {ms}  actual={x['actual_home_score']}:{x['actual_away_score']}")

    print("\n=== 近 30 天汇总 ===")
    since = datetime.now(BJ).replace(tzinfo=None) - timedelta(days=30)
    r = await conn.fetchrow(
        """SELECT
              count(*) AS total_settled,
              count(*) FILTER (WHERE m.status='scheduled') AS still_scheduled,
              count(*) FILTER (WHERE m.status='scheduled' AND m.home_score IS NULL) AS scheduled_no_score
           FROM matches m JOIN predictions p ON p.match_id=m.id
           WHERE m.kickoff_time >= $1
             AND p.actual_home_score IS NOT NULL AND p.actual_away_score IS NOT NULL
             AND p.kickoff_time >= $1""", since
    )
    print(f"  近30天已结算: {r['total_settled']}, 其中 status 仍 scheduled: {r['still_scheduled']}, "
          f"且 match.home_score 为空: {r['scheduled_no_score']}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
