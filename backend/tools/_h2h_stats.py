"""检查 H2H stats 字段"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_h2h_stats.txt"), "w", encoding="utf-8")

def log(msg):
    OUT.write(str(msg) + "\n")
    OUT.flush()

async def main():
    from app.db.database import async_session
    from sqlalchemy import text
    
    async with async_session() as db:
        # 查 team 25 vs 34 的 H2H 记录，看 stats 字段
        result = await db.execute(text(
            "SELECT id, home_team_id, away_team_id, home_score, away_score, match_date, "
            "sportmonks_fixture_id, "
            "CASE WHEN home_stats IS NULL THEN 'NULL' WHEN home_stats::text = '{}' THEN 'empty' ELSE 'has_data' END as hs, "
            "CASE WHEN away_stats IS NULL THEN 'NULL' WHEN away_stats::text = '{}' THEN 'empty' ELSE 'has_data' END as aws "
            "FROM head_to_head "
            "WHERE (home_team_id = 25 AND away_team_id = 34) OR (home_team_id = 34 AND away_team_id = 25) "
            "ORDER BY match_date DESC LIMIT 10"
        ))
        rows = result.fetchall()
        log("H2H team 25 vs 34:")
        for r in rows:
            log(f"  id={r[0]} {r[1]}-{r[2]} {r[3]}-{r[4]} {r[5]} sm_id={r[6]} hs={r[7]} aws={r[8]}")
        
        # 看看全局 H2H 表中有多少有 stats 数据
        result = await db.execute(text(
            "SELECT "
            "COUNT(*) as total, "
            "SUM(CASE WHEN home_stats IS NOT NULL AND home_stats::text != '{}' THEN 1 ELSE 0 END) as with_home_stats, "
            "SUM(CASE WHEN sportmonks_fixture_id IS NOT NULL THEN 1 ELSE 0 END) as with_sm_id "
            "FROM head_to_head"
        ))
        r = result.fetchone()
        log(f"\nH2H 全局: total={r[0]}, with_stats={r[1]}, with_sm_fixture_id={r[2]}")
        
        # 查 match 4543 对应的 H2H stats
        result = await db.execute(text(
            "SELECT id, home_stats, away_stats FROM head_to_head "
            "WHERE (home_team_id = 25 AND away_team_id = 34) OR (home_team_id = 34 AND away_team_id = 25) "
            "ORDER BY match_date DESC LIMIT 3"
        ))
        rows = result.fetchall()
        log(f"\nH2H raw stats sample:")
        for r in rows:
            log(f"  id={r[0]} home_stats={str(r[1])[:200]} away_stats={str(r[2])[:200]}")

    log("\nDONE")

asyncio.run(main())
