"""直接查 H2H 表 v2"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_h2h_check.txt"), "w", encoding="utf-8")

def log(msg):
    OUT.write(str(msg) + "\n")
    OUT.flush()

async def main():
    from app.db.database import async_session
    from sqlalchemy import select, text
    from app.db.models import Match, HeadToHead
    
    async with async_session() as db:
        m = (await db.execute(select(Match).where(Match.id == 15465))).scalar_one_or_none()
        log(f"Match 15465: home_team_id={m.home_team_id}, away_team_id={m.away_team_id}")
        
        # Direct SQL
        result = await db.execute(text(
            "SELECT id, home_team_id, away_team_id, home_score, away_score, match_date, home_stats, away_stats "
            "FROM head_to_head WHERE "
            "(home_team_id = :h1 AND away_team_id = :a1) OR "
            "(home_team_id = :a2 AND away_team_id = :h2) "
            "ORDER BY match_date DESC LIMIT 10"
        ), {"h1": m.home_team_id, "a1": m.away_team_id, "h2": m.home_team_id, "a2": m.away_team_id})
        rows = result.fetchall()
        log(f"H2H records: {len(rows)}")
        for r in rows:
            log(f"  id={r[0]} home={r[1]} away={r[2]} score={r[3]}-{r[4]} date={r[5]}")
            log(f"    home_stats={r[6]}")
            log(f"    away_stats={r[7]}")

asyncio.run(main())
