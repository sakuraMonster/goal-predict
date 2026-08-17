"""最终验证：08-16 比赛日 status 已 finished + 3 场还原值完好"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 08-16 比赛日 (08-16 12:00 ~ 08-17 12:00) 状态 ===")
    rows = await conn.fetch(
        """SELECT m.id, m.kickoff_time, m.status, m.home_score, m.away_score,
                  p.expected_goals_c, p.snap_top2_c, p.actual_total_goals
           FROM matches m LEFT JOIN predictions p ON p.match_id=m.id
           WHERE m.kickoff_time >= '2026-08-16 12:00' AND m.kickoff_time < '2026-08-17 12:00'
           ORDER BY m.kickoff_time"""
    )
    still = 0
    for r in rows:
        flag = ""
        if r["status"] != "finished":
            flag = "  ★ 非finished"
            still += 1
        print(f"  #{r['id']} {r['kickoff_time']} [{r['status']}] {r['home_score']}:{r['away_score']} "
              f"exp_c={r['expected_goals_c']} snap_c={r['snap_top2_c']} actual={r['actual_total_goals']}{flag}")
    print(f"  非 finished 场次: {still}")

    print("\n=== 3 场还原值确认 ===")
    for mid in (15672, 15648, 15674):
        r = await conn.fetchrow(
            "SELECT m.status, p.expected_goals_c, p.snap_top2_c FROM matches m JOIN predictions p ON p.match_id=m.id WHERE m.id=$1", mid
        )
        print(f"  #{mid}: status={r['status']} exp_c={r['expected_goals_c']} snap_c={r['snap_top2_c']}")

    print("\n=== 全量检查：status=scheduled 但已结算（应=0） ===")
    r = await conn.fetchrow(
        """SELECT count(*) AS n FROM matches m JOIN predictions p ON p.match_id=m.id
           WHERE m.status='scheduled' AND p.actual_home_score IS NOT NULL AND p.actual_away_score IS NOT NULL"""
    )
    print(f"  残留: {r['n']}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
