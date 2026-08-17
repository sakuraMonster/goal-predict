"""还原 08-17 10:40/10:41 predict-model-c 覆盖的 08-16 比赛日预测值

还原来源：goal_pick_records（pick_date=08-16 推荐时快照，保存了当时的
expected_goals_c + snap_top2_c）。仅该表保存了历史预测快照，其余场次无旧值来源。
"""
import asyncio
import asyncpg
from datetime import datetime, timedelta, timezone

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
BJ = timezone(timedelta(hours=8))

# 08-16 比赛日窗口（竞彩 12:00 口径）
D_START = datetime(2026, 8, 16, 12, 0, 0)
D_END = datetime(2026, 8, 17, 12, 0, 0)


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 1. 快照场次对比：goal_pick_records(pick_date=08-16) vs predictions 当前值 ===")
    snap_rows = await conn.fetch(
        """SELECT match_id, rank, home_team, away_team, expected_goals_c, snap_top2_c
           FROM goal_pick_records WHERE pick_date='2026-08-16' ORDER BY rank"""
    )
    for r in snap_rows:
        cur = await conn.fetchrow(
            "SELECT expected_goals_c, snap_top2_c FROM predictions WHERE match_id=$1", r["match_id"]
        )
        old = round(r["expected_goals_c"], 4) if r["expected_goals_c"] is not None else None
        now_val = round(cur["expected_goals_c"], 4) if cur and cur["expected_goals_c"] is not None else None
        same = (old == now_val) and (r["snap_top2_c"] == (cur["snap_top2_c"] if cur else None))
        print(f"  rank={r['rank']} match={r['match_id']} {r['home_team']} vs {r['away_team']}")
        print(f"     快照: exp_c={old} snap_c={r['snap_top2_c']}")
        print(f"     当前: exp_c={now_val} snap_c={cur['snap_top2_c'] if cur else None}  {'一致' if same else '★★ 被覆盖 ★★'}")

    print("\n=== 2. 08-16 比赛日全部场次当前 exp_c/snap_c（供核对，无快照场次旧值不可得） ===")
    rows = await conn.fetch(
        """
        SELECT m.id, m.kickoff_time, m.status,
               l.name_zh AS lg, th.name_zh AS home, ta.name_zh AS away,
               p.expected_goals_c, p.snap_top2_c, p.actual_total_goals
        FROM matches m
        LEFT JOIN leagues l ON l.id=m.league_id
        LEFT JOIN teams th ON th.id=m.home_team_id
        LEFT JOIN teams ta ON ta.id=m.away_team_id
        LEFT JOIN predictions p ON p.match_id=m.id
        WHERE m.kickoff_time >= $1 AND m.kickoff_time < $2
        ORDER BY m.kickoff_time
        """, D_START, D_END,
    )
    for r in rows:
        print(f"  #{r['id']} {r['kickoff_time']} [{r['status']}] {r['home']} vs {r['away']} "
              f"exp_c={r['expected_goals_c']} snap_c={r['snap_top2_c']} actual={r['actual_total_goals']}")

    print("\n=== 3. 执行还原（仅快照场次） ===")
    restored = 0
    for r in snap_rows:
        cur = await conn.fetchrow("SELECT expected_goals_c, snap_top2_c FROM predictions WHERE match_id=$1", r["match_id"])
        old_exp = round(r["expected_goals_c"], 4) if r["expected_goals_c"] is not None else None
        now_exp = round(cur["expected_goals_c"], 4) if cur and cur["expected_goals_c"] is not None else None
        old_snap = r["snap_top2_c"]
        now_snap = cur["snap_top2_c"] if cur else None
        if old_exp == now_exp and old_snap == now_snap:
            print(f"  match={r['match_id']} 无需还原（值已一致）")
            continue
        await conn.execute(
            "UPDATE predictions SET expected_goals_c=$1, snap_top2_c=$2 WHERE match_id=$3",
            old_exp, old_snap, r["match_id"],
        )
        restored += 1
        print(f"  match={r['match_id']} 已还原: exp_c {now_exp}→{old_exp}, snap_c {now_snap}→{old_snap}")

    await conn.close()
    print(f"\n共还原 {restored} 场")


if __name__ == "__main__":
    asyncio.run(main())
