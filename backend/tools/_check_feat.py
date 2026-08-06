import asyncio, sys
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')
from app.db.database import async_session
from app.predictor.features import FeatureEngineer

async def main():
    async with async_session() as db:
        eng = FeatureEngineer(db)
        for mid in [15463, 15465]:
            f = await eng.extract_features(mid)
            if f.empty: continue
            r = f.iloc[0]
            sl = r.get('season_match_same_league', 'MISSING')
            lid = r.get('league_id', '?')
            hgp = r.get('home_games_played', '?')
            agp = r.get('away_games_played', '?')
            print(f'ID={mid}: season_match_same_league={sl}, league_id={lid}, home_gp={hgp}, away_gp={agp}')

asyncio.run(main())
