"""验证跨联赛H2H权重提升 - 精简版"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.db.database import async_session
from app.predictor.features import FeatureEngineer
from sqlalchemy import select
from app.db.models import Match

async def main():
    async with async_session() as db:
        fe = FeatureEngineer(db)
        
        mid = 15465
        match = (await db.execute(select(Match).where(Match.id == mid))).scalar_one_or_none()
        if match:
            print(f"Match: {match.home_team} vs {match.away_team}, league={match.league_id}")
        
        features_df = await fe.extract_features(mid)
        if features_df.empty:
            print("ERROR: features_df is empty!")
            return
        
        f = features_df.iloc[0].to_dict()
        
        print(f"same_league: {f.get('season_match_same_league')}")
        data_trust = 0.4 + 0.6 * (f.get('season_match_same_league', 0) or 0)
        print(f"data_trust: {data_trust:.2f}")
        print(f"home_win_rate: {f.get('home_win_rate')}, away_win_rate: {f.get('away_win_rate')}")
        print(f"home_rank: {f.get('home_league_rank')}, away_rank: {f.get('away_league_rank')}")
        print(f"league_rank_gap: {f.get('league_rank_gap')}")
        print(f"h2h_home_win_pct: {f.get('h2h_home_win_pct')}, h2h_away_win_pct: {f.get('h2h_away_win_pct')}")
        h2h_balance = (f.get('h2h_home_win_pct', 0.33) or 0) - (f.get('h2h_away_win_pct', 0.33) or 0)
        print(f"h2h_balance: {h2h_balance:.4f}")
        print(f"home_goals_avg: {f.get('home_goals_avg')}, away_goals_against_avg: {f.get('away_goals_against_avg')}")
        print(f"odds_movement_home: {f.get('odds_movement_home')}, odds_movement_away: {f.get('odds_movement_away')}")
        print(f"fundamental_score: {f.get('fundamental_score')}")
        print(f"market_direction_score: {f.get('market_direction_score')}")
        print(f"fundamental_vs_market_divergence: {f.get('fundamental_vs_market_divergence')}")

asyncio.run(main())
