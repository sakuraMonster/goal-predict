"""验证有 H2H 数据的比赛"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_h2h_verify.txt"), "w", encoding="utf-8")

def log(msg):
    OUT.write(str(msg) + "\n")
    OUT.flush()

async def main():
    from app.db.database import async_session
    from app.predictor.features import FeatureEngineer
    from sqlalchemy import select, text
    
    async with async_session() as db:
        fe = FeatureEngineer(db)
        
        # 找一场肯定有H2H的：查 H2H 表里出现频率最高的对阵组合
        result = await db.execute(text(
            "SELECT home_team_id, away_team_id, COUNT(*) as cnt "
            "FROM head_to_head GROUP BY home_team_id, away_team_id "
            "ORDER BY cnt DESC LIMIT 5"
        ))
        popular_h2h = result.fetchall()
        log("H2H 最常见对阵组合:")
        for r in popular_h2h:
            log(f"  {r[0]} vs {r[1]}: {r[2]}场")
        
        # 用第一对组合找最新的 match
        if popular_h2h:
            ht_id, at_id = popular_h2h[0][0], popular_h2h[0][1]
            result = await db.execute(text(
                "SELECT id, home_team_id, away_team_id FROM matches "
                "WHERE (home_team_id = :h AND away_team_id = :a) "
                "OR (home_team_id = :a2 AND away_team_id = :h2) "
                "ORDER BY kickoff_time DESC LIMIT 3"
            ), {"h": ht_id, "a": at_id, "a2": at_id, "h2": ht_id})
            matches = result.fetchall()
            
            for m in matches:
                mid = m[0]
                log(f"\n--- Match {mid} (home={m[1]}, away={m[2]}) ---")
                features_df = await fe.extract_features(mid)
                f = features_df.iloc[0].to_dict()
                
                log(f"has_h2h: {f.get('has_h2h')}")
                log(f"h2h_match_count: {f.get('h2h_match_count')}")
                log(f"h2h_avg_home_xg: {f.get('h2h_avg_home_xg')}, h2h_avg_away_xg: {f.get('h2h_avg_away_xg')}")
                log(f"h2h_avg_xg_diff: {f.get('h2h_avg_xg_diff')}")
                log(f"h2h_avg_home_shots: {f.get('h2h_avg_home_shots')}, away: {f.get('h2h_avg_away_shots')}")
                log(f"h2h_avg_home_possession: {f.get('h2h_avg_home_possession')}")
                log(f"h2h_avg_home_dangerous: {f.get('h2h_avg_home_dangerous')}, away: {f.get('h2h_avg_away_dangerous')}")
                log(f"fundamental_score: {f.get('fundamental_score')}")
                log(f"fundamental_vs_market_divergence: {f.get('fundamental_vs_market_divergence')}")

    log("\nDONE")

asyncio.run(main())
