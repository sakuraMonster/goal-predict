"""修复：已完场（predictions.actual 非空）但 matches.status 仍为 scheduled 的场次

- 将 status 改为 finished
- 回填 matches.home_score / away_score（此前从未回写，导致联赛基线聚合缺失近期数据）

只处理 predictions 已有 actual 比分的场次（幂等）。
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    rows = await conn.fetch(
        """SELECT m.id, m.kickoff_time, m.status, m.home_score, m.away_score,
                  p.actual_home_score, p.actual_away_score
           FROM matches m JOIN predictions p ON p.match_id=m.id
           WHERE p.actual_home_score IS NOT NULL AND p.actual_away_score IS NOT NULL
             AND (m.status != 'finished' OR m.home_score IS NULL)
           ORDER BY m.kickoff_time"""
    )
    print(f"待修复: {len(rows)} 场")

    fixed = 0
    for x in rows:
        if x["actual_home_score"] is None or x["actual_away_score"] is None:
            continue
        await conn.execute(
            "UPDATE matches SET status='finished', home_score=$1, away_score=$2, updated_at=now() WHERE id=$3",
            x["actual_home_score"], x["actual_away_score"], x["id"],
        )
        fixed += 1

    await conn.close()
    print(f"已修复 {fixed} 场: status='finished' + 比分回填")


if __name__ == "__main__":
    asyncio.run(main())
