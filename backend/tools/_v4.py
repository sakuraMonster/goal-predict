"""验证 H2H 修复效果"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_v4out.txt"), "w", encoding="utf-8")

def log(msg):
    OUT.write(str(msg) + "\n")
    OUT.flush()

async def main():
    try:
        from app.db.database import async_session
        from app.predictor.features import FeatureEngineer
        from sqlalchemy import select, text
        
        async with async_session() as db:
            fe = FeatureEngineer(db)
            
            # 测试1: team 25 vs 34 的 match（有9场H2H，stats为空）
            result = await db.execute(text(
                "SELECT id FROM matches WHERE home_team_id=25 AND away_team_id=34 "
                "ORDER BY kickoff_time DESC LIMIT 1"
            ))
            mid = result.scalar()
            log(f"=== Match {mid} (team 25 vs 34, 9 H2H records, stats=NULL) ===")
            features_df = await fe.extract_features(mid)
            f = features_df.iloc[0].to_dict()
            log(f"has_h2h: {f.get('has_h2h')}, match_count: {f.get('h2h_match_count')}")
            log(f"h2h_stats_available: {f.get('h2h_stats_available')}")
            log(f"h2h_home_win_rate: {f.get('h2h_home_win_rate')}, away: {f.get('h2h_away_win_rate')}, draw: {f.get('h2h_draw_rate')}")
            log(f"h2h_avg_home_goals: {f.get('h2h_avg_home_goals')}, away: {f.get('h2h_avg_away_goals')}")
            log(f"h2h_avg_home_xg: {f.get('h2h_avg_home_xg')}, away: {f.get('h2h_avg_away_xg')}")
            log(f"fundamental_score: {f.get('fundamental_score')}")
            log(f"market_direction_score: {f.get('market_direction_score')}")
            log(f"fundamental_vs_market_divergence: {f.get('fundamental_vs_market_divergence')}")
            
            # 测试2: 周四003 (无H2H)
            log(f"\n=== Match 15465 (安德莱赫特 vs 哈马比, 无H2H) ===")
            features_df = await fe.extract_features(15465)
            f = features_df.iloc[0].to_dict()
            log(f"has_h2h: {f.get('has_h2h')}, match_count: {f.get('h2h_match_count')}")
            log(f"h2h_stats_available: {f.get('h2h_stats_available')}")
            log(f"h2h_home_win_rate: {f.get('h2h_home_win_rate')}, away: {f.get('h2h_away_win_rate')}")
            log(f"h2h_avg_home_goals: {f.get('h2h_avg_home_goals')}, away: {f.get('h2h_avg_away_goals')}")
            log(f"fundamental_score: {f.get('fundamental_score')}")
            log(f"fundamental_vs_market_divergence: {f.get('fundamental_vs_market_divergence')}")
            
    except Exception as e:
        import traceback
        log(f"ERROR: {e}")
        traceback.print_exc(file=OUT)
        OUT.flush()

asyncio.run(main())
