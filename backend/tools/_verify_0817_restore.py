"""最终验证：3 场还原值完好 + 08-17 未开赛场次 prediction 状态"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 还原场次确认（应等于快照 2.8/2.438/2.1068） ===")
    for mid in (15672, 15648, 15674):
        r = await conn.fetchrow(
            "SELECT match_id, expected_goals_c, snap_top2_c, created_at FROM predictions WHERE match_id=$1", mid
        )
        print(f"  #{r['match_id']}: exp_c={r['expected_goals_c']} snap_c={r['snap_top2_c']} created={r['created_at']}")

    print("\n=== 08-17 未开赛 scheduled 场次 prediction 状态 ===")
    rows = await conn.fetch(
        """SELECT m.id, m.kickoff_time, p.id AS pid, p.expected_goals_c, p.snap_top2_c
           FROM matches m LEFT JOIN predictions p ON p.match_id=m.id
           WHERE m.kickoff_time >= '2026-08-17 12:00' AND m.kickoff_time < '2026-08-18 12:00'
           ORDER BY m.kickoff_time"""
    )
    for r in rows:
        print(f"  #{r['id']} {r['kickoff_time']} pid={r['pid']} exp_c={r['expected_goals_c']} snap_c={r['snap_top2_c']}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
