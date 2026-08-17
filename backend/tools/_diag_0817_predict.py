"""诊断 08-17 点击"预测"后 08-16 比赛日数据是否被 predict-model-c 覆盖"""
import asyncio
import asyncpg
import json
from datetime import datetime, timedelta, timezone

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
BJ = timezone(timedelta(hours=8))


async def main():
    conn = await asyncpg.connect(DSN)

    now_bj = datetime.now(BJ)
    print(f"当前北京时间: {now_bj:%Y-%m-%d %H:%M:%S}")
    print(f"当前 UTC: {datetime.utcnow():%Y-%m-%d %H:%M:%S}")

    # 模拟 predict-model-c 查询窗口（与 admin.py 逻辑一致）
    qs = now_bj.replace(hour=12, minute=0, second=0, microsecond=0)
    if now_bj.hour < 12:
        qs = qs - timedelta(days=1)
    qe = qs + timedelta(hours=72)
    print(f"predict-model-c 查询窗口: {qs:%Y-%m-%d %H:%M} ~ {qe:%Y-%m-%d %H:%M}")

    print("\n=== 1. 最近 predict 相关日志 (task_logs) ===")
    rows = await conn.fetch(
        """SELECT id, task_type, status, start_time, duration_ms, message
           FROM task_logs
           WHERE task_type IN ('predict', 'predict-model-c') OR message LIKE '%Model C%'
           ORDER BY id DESC LIMIT 15"""
    )
    for r in rows:
        print(f"  #{r['id']} {r['start_time']} | {r['task_type']} | {r['message']}")

    print("\n=== 2. 08-16 比赛日 (08-16 12:00 ~ 08-17 12:00) 全部比赛 + 预测 ===")
    d_start = datetime(2026, 8, 16, 12, 0, 0)
    d_end = datetime(2026, 8, 17, 12, 0, 0)
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.kickoff_time, m.status,
               l.name_zh AS lg, th.name_zh AS home, ta.name_zh AS away,
               p.id AS pid, p.model_version, p.created_at,
               p.expected_goals_c, p.snap_top2_c, p.snap_top2,
               p.actual_home_score, p.actual_away_score, p.actual_total_goals, p.result_goals
        FROM matches m
        LEFT JOIN leagues l ON l.id=m.league_id
        LEFT JOIN teams th ON th.id=m.home_team_id
        LEFT JOIN teams ta ON ta.id=m.away_team_id
        LEFT JOIN predictions p ON p.match_id=m.id
        WHERE m.kickoff_time >= $1 AND m.kickoff_time < $2
        ORDER BY m.kickoff_time
        """, d_start, d_end,
    )
    for r in rows:
        sc = r["snap_top2_c"]
        print(f"  #{r['id']} {r['kickoff_time']} [{r['status']}] {r['home']} vs {r['away']} ({r['lg']})")
        print(f"      model_version={r['model_version']} created={r['created_at']}")
        print(f"      exp_c={r['expected_goals_c']} snap_c={sc} snap_b={r['snap_top2']} "
              f"actual={r['actual_home_score']}:{r['actual_away_score']} total={r['actual_total_goals']} result_goals={r['result_goals']}")

    print("\n=== 3. goal_pick_records 推荐快照 (pick_date 08-15 ~ 08-17) ===")
    rows = await conn.fetch(
        """SELECT id, pick_date, rank, match_id, match_num, league_name, home_team, away_team,
                  kickoff_time, expected_goals_c, snap_top2_c, score, updated_at
           FROM goal_pick_records
           WHERE pick_date >= '2026-08-15' AND pick_date <= '2026-08-17'
           ORDER BY pick_date DESC, rank ASC"""
    )
    for r in rows:
        print(f"  pick_date={r['pick_date']} rank={r['rank']} match={r['match_id']} "
              f"{r['home_team']} vs {r['away_team']} exp_c={r['expected_goals_c']} "
              f"snap_c={r['snap_top2_c']} score={r['score']} updated={r['updated_at']}")

    print("\n=== 4. 08-17 比赛日 scheduled 比赛 (当前窗口) ===")
    d_start2 = datetime(2026, 8, 17, 12, 0, 0)
    d_end2 = datetime(2026, 8, 18, 12, 0, 0)
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.kickoff_time, m.status,
               l.name_zh AS lg, th.name_zh AS home, ta.name_zh AS away
        FROM matches m
        LEFT JOIN leagues l ON l.id=m.league_id
        LEFT JOIN teams th ON th.id=m.home_team_id
        LEFT JOIN teams ta ON ta.id=m.away_team_id
        WHERE m.kickoff_time >= $1 AND m.kickoff_time < $2
        ORDER BY m.kickoff_time
        """, d_start2, d_end2,
    )
    for r in rows:
        print(f"  #{r['id']} {r['kickoff_time']} [{r['status']}] {r['home']} vs {r['away']} ({r['lg']})")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
