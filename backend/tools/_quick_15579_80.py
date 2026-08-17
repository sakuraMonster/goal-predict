"""15579/15580 快速速览: 当前 OU 特征 + λ 分解"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_c import ModelC
from app.predictor.snap import snap_top2


async def main():
    async with async_session() as db:
        feat = FeatureEngineerB(db)
        model_c = ModelC()
        for mid, lg in [(15579, "瑞典超"), (15580, "葡超")]:
            fdf = await feat.extract_features(mid)
            if fdf.empty:
                print(f"{mid}: features EMPTY")
                continue
            f = fdf.iloc[0].to_dict()
            rc = model_c.predict(f, lg)
            d = rc["detail"]
            print(f"\n{mid} ({lg}): λ_c={rc['expected_goals']} top2={snap_top2(rc['expected_goals'])}")
            for k in ["goal_line", "calib", "strength_adj", "form_adj", "drop_adj",
                      "lambda_market", "lambda_fundamental", "divergence", "induce_score",
                      "market_confidence", "market_weight", "early_season_applied",
                      "home_goals_avg", "away_goals_avg", "home_gf_avg_6", "away_gf_avg_6"]:
                print(f"    {k} = {d.get(k)}")


asyncio.run(main())
