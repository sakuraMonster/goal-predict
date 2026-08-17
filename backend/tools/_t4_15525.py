"""t4 深挖：15525 埃门vs罗达JC λ偏高原因 —— 检查荷乙球队 rm 特征"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import async_session
from app.predictor.features_b import FeatureEngineerB


async def main():
    async with async_session() as db:
        feat = FeatureEngineerB(db)
        fdf = await feat.extract_features(15525)
        if fdf.empty:
            print("FEATURES EMPTY")
            return
        r = fdf.iloc[0]
        for k in ("home_goals_avg", "away_goals_avg", "home_goals_against_avg", "away_goals_against_avg",
                  "home_gf_avg_6", "away_gf_avg_6", "goal_line_market", "league_avg_total_goals",
                  "home_games_played", "away_games_played", "home_form_pts_6", "away_form_pts_6"):
            print(f"{k}: {r.get(k)}")


asyncio.run(main())
