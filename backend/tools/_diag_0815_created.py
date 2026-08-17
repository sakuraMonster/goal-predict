"""08-15 周期比赛入库时间 + match_fixtures 匹配窗口分析"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.kickoff_time, m.created_at, m.updated_at,
               m.jc_match_id, m.sportmonks_fixture_id AS fx, m.status
        FROM matches m
        WHERE m.kickoff_time >= '2026-08-15 12:00:00' AND m.kickoff_time < '2026-08-16 12:00:00'
        ORDER BY m.created_at
        """
    )
    print("=== 08-15 周期比赛入库时间 ===")
    for r in rows:
        print(f"  #{r['id']} {r['match_num']:<8} {r['jc_match_id']} created={r['created_at']} updated={r['updated_at']} ko={r['kickoff_time']} fx={r['fx']} status={r['status']}")

    print("\n=== 近24h created 的比赛 ===")
    rows2 = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.created_at, m.kickoff_time, l.name_zh
        FROM matches m LEFT JOIN leagues l ON l.id=m.league_id
        WHERE m.created_at >= '2026-08-14 11:00:00'
        ORDER BY m.created_at
        """
    )
    for r in rows2:
        print(f"  #{r['id']} {r['match_num']:<8} created={r['created_at']} ko={r['kickoff_time']} [{r['name_zh']}]")

    print("\n=== 当前 utcnow / 北京时间 ===")
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    now_bj = now_utc.astimezone(timezone(timedelta(hours=8)))
    print(f"  utcnow={now_utc} 北京={now_bj}")
    print(f"  match_fixtures 窗口 kickoff >= utcnow-1day = {now_utc.replace(tzinfo=None) - timedelta(days=1)} (UTC naive 比较)")

    await conn.close()


asyncio.run(main())
