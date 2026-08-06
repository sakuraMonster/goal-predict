"""验证跨联赛H2H权重提升 + 诱盘检测效果"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.db.database import async_session
from app.predictor.pipeline import PredictionPipeline

async def main():
    async with async_session() as db:
        pipeline = PredictionPipeline(db)
        
        # 周四003: 安德莱赫特 vs 哈马比
        mid = 15465
        result = await pipeline.predict(mid)
        
        print(f"=== 周四003 安德莱赫特 vs 哈马比 ===")
        print(f"SPF: 主{result.get('home_prob',0):.3f} 平{result.get('draw_prob',0):.3f} 客{result.get('away_prob',0):.3f}")
        print(f"冷门: {result.get('is_cold_match')}")
        print(f"预期进球: {result.get('expected_goals', 0):.2f}")
        
        # 诱盘检测结果
        fe = pipeline.feature_engineer
        from sqlalchemy import select
        from app.db.models import Match
        match = (await db.execute(select(Match).where(Match.id == mid))).scalar_one_or_none()
        
        # 提取特征看 fundamental_score 和 divergence
        import pandas as pd
        features_df = await fe.extract_features(mid)
        if not features_df.empty:
            f = features_df.iloc[0].to_dict()
            print(f"\n--- 诱盘检测特征 ---")
            print(f"same_league: {f.get('season_match_same_league')}")
            print(f"data_trust: {0.4 + 0.6 * (f.get('season_match_same_league', 0) or 0):.2f}")
            print(f"home_win_rate: {f.get('home_win_rate')}, away_win_rate: {f.get('away_win_rate')}")
            print(f"home_rank: {f.get('home_league_rank')}, away_rank: {f.get('away_league_rank')}")
            print(f"league_rank_gap: {f.get('league_rank_gap')}")
            print(f"h2h_home_win_pct: {f.get('h2h_home_win_pct')}, h2h_away_win_pct: {f.get('h2h_away_win_pct')}")
            print(f"home_goals_avg: {f.get('home_goals_avg')}, away_goals_against_avg: {f.get('away_goals_against_avg')}")
            print(f"odds_movement_home: {f.get('odds_movement_home')}, odds_movement_away: {f.get('odds_movement_away')}")
            print(f"fundamental_score: {f.get('fundamental_score')}")
            print(f"market_direction_score: {f.get('market_direction_score')}")
            print(f"fundamental_vs_market_divergence: {f.get('fundamental_vs_market_divergence')}")
        
        # 冷门修正详情
        cc = result.get("cold_correction", {})
        if cc:
            print(f"\n--- 冷门修正 ---")
            print(f"divergence_direction: {cc.get('divergence_direction')}")
            print(f"intent_applied: {cc.get('intent_applied')}")

asyncio.run(main())
