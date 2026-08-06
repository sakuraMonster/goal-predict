"""深度排查 H2H - 修复列名"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_h2h_debug.txt"), "w", encoding="utf-8")

def log(msg):
    OUT.write(str(msg) + "\n")
    OUT.flush()

async def main():
    from app.db.database import async_session
    from sqlalchemy import select, text, func
    
    async with async_session() as db:
        # 1. match 15465
        result = await db.execute(text(
            "SELECT m.id, m.home_team_id, m.away_team_id, "
            "ht.name_zh as hz, ht.name_en as he, "
            "at.name_zh as az, at.name_en as ae "
            "FROM matches m "
            "JOIN teams ht ON m.home_team_id = ht.id "
            "JOIN teams at ON m.away_team_id = at.id "
            "WHERE m.id = 15465"
        ))
        row = result.fetchone()
        mid, home_id, away_id, hz, he, az, ae = row
        log(f"Match 15465: {hz}/{he}(id={home_id}) vs {az}/{ae}(id={away_id})")
        
        # 2. H2H 总记录
        result = await db.execute(text("SELECT COUNT(*) FROM head_to_head"))
        log(f"\nH2H 总记录: {result.scalar()}")
        
        # 3. 查 H2H 中 home_team_id 的分布（前20）
        result = await db.execute(text(
            "SELECT home_team_id, COUNT(*) as cnt FROM head_to_head "
            "GROUP BY home_team_id ORDER BY cnt DESC LIMIT 20"
        ))
        rows = result.fetchall()
        log(f"\nH2H home_team_id 分布 (top 20):")
        for r in rows:
            log(f"  team_id={r[0]} count={r[1]}")
        
        # 4. 按名称模糊查 H2H
        result = await db.execute(text(
            "SELECT h.id, h.home_team_id, h.away_team_id, h.home_score, h.away_score, h.match_date, "
            "ht.name_zh as hz, at.name_zh as az "
            "FROM head_to_head h "
            "JOIN teams ht ON h.home_team_id = ht.id "
            "JOIN teams at ON h.away_team_id = at.id "
            "WHERE (ht.name_zh LIKE :q1 OR ht.name_en LIKE :q2) "
            "AND (at.name_zh LIKE :q3 OR at.name_en LIKE :q4) "
            "LIMIT 10"
        ), {"q1": f"%{hz}%", "q2": f"%{he}%", "q3": f"%{az}%", "q4": f"%{ae}%"})
        rows = result.fetchall()
        log(f"\nH2H 名称模糊查 ({hz}/{he} vs {az}/{ae}): {len(rows)}")
        for r in rows:
            log(f"  id={r[0]} {r[6]}({r[1]}) vs {r[7]}({r[2]}) {r[3]}-{r[4]} {r[5]}")
        
        # 5. 查 matches 表中有没有同样这两队的历史比赛
        result = await db.execute(text(
            "SELECT m.id, m.home_team_id, m.away_team_id, m.home_score, m.away_score, m.kickoff_time, "
            "m.jc_match_id "
            "FROM matches m "
            "WHERE (m.home_team_id = :h1 AND m.away_team_id = :a1) "
            "OR (m.home_team_id = :a2 AND m.away_team_id = :h2) "
            "ORDER BY m.kickoff_time DESC LIMIT 10"
        ), {"h1": home_id, "a1": away_id, "a2": away_id, "h2": home_id})
        rows = result.fetchall()
        log(f"\nmatches 表中同样两队的历史: {len(rows)}")
        for r in rows:
            log(f"  id={r[0]} home={r[1]} away={r[2]} {r[3]}-{r[4]} {r[5]} jc={r[6]}")
        
        # 6. H2H表中 team_id 546 或 153 的记录
        result = await db.execute(text(
            "SELECT id, home_team_id, away_team_id, home_score, away_score, match_date "
            "FROM head_to_head "
            "WHERE home_team_id IN (:t1, :t2) OR away_team_id IN (:t1, :t2) "
            "LIMIT 20"
        ), {"t1": home_id, "t2": away_id})
        rows = result.fetchall()
        log(f"\nH2H 中涉及 team {home_id} 或 {away_id} 的记录: {len(rows)}")
        for r in rows:
            log(f"  id={r[0]} home={r[1]} away={r[2]} {r[3]}-{r[4]} {r[5]}")
        
        # 7. 看看最近的H2H记录 (sample)
        result = await db.execute(text(
            "SELECT h.id, h.home_team_id, h.away_team_id, h.home_score, h.away_score, h.match_date, "
            "ht.name_zh as hz, at.name_zh as az "
            "FROM head_to_head h "
            "JOIN teams ht ON h.home_team_id = ht.id "
            "JOIN teams at ON h.away_team_id = at.id "
            "ORDER BY h.id DESC LIMIT 10"
        ))
        rows = result.fetchall()
        log(f"\nH2H 最新10条:")
        for r in rows:
            log(f"  id={r[0]} {r[6]}({r[1]}) vs {r[7]}({r[2]}) {r[3]}-{r[4]} {r[5]}")

    log("\nDONE")

asyncio.run(main())
