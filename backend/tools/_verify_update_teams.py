"""验证 update-teams 手动任务的实际执行效果：
1) task_logs 中 update_teams 最近记录
2) H2H home_stats 回填情况（有无 stats 计数）
3) 近期状态 recent_matches 非空情况
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 1. task_logs 中 update_teams 最近记录 ===")
    rows = await conn.fetch(
        """SELECT id, task_type, status, message, duration_ms, created_at
           FROM task_logs WHERE task_type='update_teams'
           ORDER BY id DESC LIMIT 5"""
    )
    for r in rows:
        print(f"  #{r['id']} {r['created_at'].strftime('%m-%d %H:%M:%S')} {r['status']} | {r['message']}")

    print("\n=== 2. H2H 记录 stats 覆盖情况（近7天比赛涉及球队配对）===")
    rows = await conn.fetch(
        """SELECT
             COUNT(*) AS total,
             COUNT(*) FILTER (WHERE home_stats IS NOT NULL) AS with_stats,
             COUNT(*) FILTER (WHERE home_stats IS NULL) AS without_stats
           FROM head_to_head"""
    )
    r = rows[0]
    print(f"  全表 H2H: total={r['total']}, with_stats={r['with_stats']}, without_stats={r['without_stats']}")

    print("\n=== 3. 近期状态 recent_matches 非空（top 记录）===")
    rows = await conn.fetch(
        """SELECT
             COUNT(*) AS total,
             COUNT(*) FILTER (WHERE recent_matches IS NOT NULL AND json_array_length(recent_matches::json) > 0) AS with_recent
           FROM team_season_stats"""
    )
    r = rows[0]
    print(f"  全表 stats: total={r['total']}, with_recent={r['with_recent']}")

    print("\n=== 4. 15652/15663/15665 H2H 是否已带 stats（本次任务回填）===")
    for mid in [15652, 15663, 15665]:
        r = await conn.fetchrow(
            """SELECT m.home_team_id AS h, m.away_team_id AS a
               FROM matches m WHERE m.id=$1""", mid)
        rows = await conn.fetch(
            """SELECT id, match_date, home_score, away_score,
                      (home_stats IS NOT NULL) AS has_stats
               FROM head_to_head
               WHERE (home_team_id=$1 AND away_team_id=$2) OR (home_team_id=$2 AND away_team_id=$1)
               ORDER BY match_date DESC""", r["h"], r["a"])
        for x in rows:
            print(f"  #{mid} H2H id={x['id']} {x['match_date'].date()} {x['home_score']}:{x['away_score']} stats={'有' if x['has_stats'] else '无'}")

    await conn.close()


asyncio.run(main())
