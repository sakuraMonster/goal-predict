"""回测前置检查：近 30 天窗口样本量 + 快照时间分布（防 look-ahead 评估）"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    # 近 30 天已完场（有 C 预测 + 实际）
    rows = await conn.fetch(
        """
        SELECT m.id, m.kickoff_time, p.actual_total_goals, p.expected_goals_c
        FROM matches m JOIN predictions p ON p.match_id = m.id
        WHERE m.kickoff_time >= '2026-07-16 12:00:00'
          AND m.kickoff_time < '2026-08-15 12:00:00'
          AND p.actual_total_goals IS NOT NULL AND p.expected_goals_c IS NOT NULL
        """
    )
    print(f"近30天已完场有C预测: {len(rows)}")

    # 快照时间 vs kickoff
    total_snap = 0
    post = 0
    with_snap = 0
    for r in rows:
        snap = await conn.fetch(
            "SELECT snapshot_time FROM odds_snapshots WHERE match_id=$1 AND goal_line IS NOT NULL",
            r["id"],
        )
        if snap:
            with_snap += 1
        total_snap += len(snap)
        post += sum(1 for s in snap if s["snapshot_time"] > r["kickoff_time"])
    print(f"有OU快照场次: {with_snap}/{len(rows)}")
    print(f"快照总数: {total_snap}, 其中 snapshot_time > kickoff_time: {post} "
          f"({post/max(total_snap,1)*100:.1f}%)")

    # 快照批次时间分布样例（最近几场）
    print("\n样例: 最近5场 kickoff vs 快照首末时间")
    recent = rows[-5:]
    for r in recent:
        snap = await conn.fetch(
            "SELECT min(snapshot_time) t0, max(snapshot_time) t1, count(*) n "
            "FROM odds_snapshots WHERE match_id=$1 AND goal_line IS NOT NULL",
            r["id"],
        )
        s = snap[0]
        print(f"  #{r['id']} kickoff={r['kickoff_time']} 快照[{s['t0']} ~ {s['t1']}] n={s['n']}")
    await conn.close()


asyncio.run(main())
