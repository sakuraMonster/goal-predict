"""验证跨联赛H2H权重提升 - 带flush和错误处理"""
import asyncio, sys, os, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

async def main():
    try:
        from app.db.database import async_session
        from app.predictor.features import FeatureEngineer
        from sqlalchemy import select
        from app.db.models import Match
        
        print("Step 1: Connecting to DB...", flush=True)
        async with async_session() as db:
            print("Step 2: Creating feature engineer...", flush=True)
            fe = FeatureEngineer(db)
            
            mid = 15465
            print(f"Step 3: Querying match {mid}...", flush=True)
            match = (await db.execute(select(Match).where(Match.id == mid))).scalar_one_or_none()
            if match:
                print(f"Match: {match.home_team} vs {match.away_team}, league={match.league_id}", flush=True)
            else:
                print("Match not found!", flush=True)
                return
            
            print(f"Step 4: Extracting features...", flush=True)
            features_df = await fe.extract_features(mid)
            if features_df.empty:
                print("ERROR: features_df is empty!", flush=True)
                return
            
            print(f"Step 5: Got {len(features_df)} rows, {len(features_df.columns)} columns", flush=True)
            f = features_df.iloc[0].to_dict()
            
            same_league = f.get('season_match_same_league', 0) or 0
            data_trust = 0.4 + 0.6 * same_league
            print(f"same_league: {same_league}", flush=True)
            print(f"data_trust: {data_trust:.2f}", flush=True)
            print(f"home_win_rate: {f.get('home_win_rate')}, away_win_rate: {f.get('away_win_rate')}", flush=True)
            print(f"home_rank: {f.get('home_league_rank')}, away_rank: {f.get('away_league_rank')}", flush=True)
            print(f"league_rank_gap: {f.get('league_rank_gap')}", flush=True)
            h_h2h = f.get('h2h_home_win_pct', 0.33) or 0.33
            a_h2h = f.get('h2h_away_win_pct', 0.33) or 0.33
            print(f"h2h_home_win_pct: {h_h2h}, h2h_away_win_pct: {a_h2h}", flush=True)
            h2h_balance = h_h2h - a_h2h
            print(f"h2h_balance: {h2h_balance:.4f}", flush=True)
            print(f"home_goals_avg: {f.get('home_goals_avg')}, away_goals_against_avg: {f.get('away_goals_against_avg')}", flush=True)
            print(f"odds_movement_home: {f.get('odds_movement_home')}, odds_movement_away: {f.get('odds_movement_away')}", flush=True)
            print(f"fundamental_score: {f.get('fundamental_score')}", flush=True)
            print(f"market_direction_score: {f.get('market_direction_score')}", flush=True)
            print(f"fundamental_vs_market_divergence: {f.get('fundamental_vs_market_divergence')}", flush=True)
            print("DONE", flush=True)
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        traceback.print_exc()
        sys.stdout.flush()

asyncio.run(main())
