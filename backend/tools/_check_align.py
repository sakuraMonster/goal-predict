import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.db.database import async_session
from app.predictor.features import FeatureEngineer

async def main():
    async with async_session() as db:
        eng = FeatureEngineer(db)
        for mid in [15463, 15465]:
            df = await eng.extract_features(mid)
            if df.empty: continue
            r = df.iloc[0]
            print(f'ID={mid}:')
            print(f'  h2h_avg_home_xg={r.get("h2h_avg_home_xg","?"):.4f}  h2h_avg_away_xg={r.get("h2h_avg_away_xg","?"):.4f}')
            print(f'  h2h_match_count={r.get("h2h_match_count","?")}  has_h2h={r.get("has_h2h","?")}')
            print(f'  h2h_odds_alignment={r.get("h2h_odds_alignment","?")}')
            print(f'  odds_movement_home={r.get("odds_movement_home",0):+.4f}  odds_movement_away={r.get("odds_movement_away",0):+.4f}')
            print(f'  odds_home_initial={r.get("odds_home_initial",0):.3f}  odds_away_initial={r.get("odds_away_initial",0):.3f}')
            print(f'  bookmaker_intent={r.get("bookmaker_intent",0):+.2f}')

asyncio.run(main())
