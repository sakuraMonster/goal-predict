"""蔚山现代 MID=15475 盘口调整详细分析"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import joinedload
from app.db.database import engine
from app.db.models import Match, OddsSnapshot
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_b import ModelB

async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    mid = 15475
    async with sf() as db:
        m = await db.execute(select(Match).options(
            joinedload(Match.home_team), joinedload(Match.away_team)
        ).where(Match.id == mid))
        match = m.unique().scalar_one()

        feat_b = FeatureEngineerB(db)
        mb = ModelB()

        feats_df = await feat_b.extract_features(mid, None)
        feats = feats_df.iloc[0].to_dict()

        raw_lam = mb.model.predict(mb._scale_features(feats_df.reindex(columns=mb._feature_names, fill_value=0.0)))[0]
        raw_lam = max(raw_lam, 0.1); raw_lam = min(raw_lam, 8.0)

        print(f"蔚山现代 vs 安养FC  实际 3:1 (T=4)")
        print(f"raw_λ = {raw_lam:.4f}")
        print()

        # 盘口相关特征
        market_keys = [
            "goal_line_market", "goal_line_max", "goal_line_min",
            "goal_line_drop_from_peak", "goal_line_volatility",
            "over_odds_movement", "over_odds_decline_rate",
            "odds_market_home_prob", "odds_market_away_prob", "odds_market_draw_prob",
            "goal_line_current",
        ]
        print("大小球盘口特征:")
        for k in market_keys:
            v = feats.get(k, None)
            print(f"  {k:30s} = {v}")

        # 模拟 _apply_goal_market_adjustment
        goal_line = feats.get("goal_line_market", 0) or 0
        goal_drop = feats.get("goal_line_drop_from_peak", 0) or 0
        goal_max = feats.get("goal_line_max", 0) or 0
        over_move = feats.get("over_odds_movement", 0) or 0
        over_decline = feats.get("over_odds_decline_rate", 0) or 0
        goal_vol = feats.get("goal_line_volatility", 0) or 0

        print(f"\n盘口调整判断:")
        print(f"  goal_line={goal_line}, goal_max={goal_max}, goal_drop={goal_drop}")
        print(f"  over_move={over_move}, goal_vol={goal_vol}")

        if goal_line < 0.5 or goal_max < 0.5:
            print(f"  → 无有效盘口，跳过")
        else:
            market_lambda = goal_line
            # 向上修正判断
            if market_lambda >= 2.5 and raw_lam < market_lambda * 0.7:
                gap = (market_lambda - raw_lam) / max(market_lambda, 1.0)
                weight = min(0.15 + 0.25 * gap, 0.40)
                adj_up = (1-weight)*raw_lam + weight*market_lambda
                print(f"  → 向上修正: market_λ={market_lambda} > model_λ={raw_lam:.2f}*0.7={raw_lam*0.7:.2f}")
                print(f"     gap={gap:.3f}, weight={weight:.2f}, adj={adj_up:.2f}")
            else:
                # 向下修正
                drop_strength = 0.0
                if goal_drop > 0.5:
                    drop_strength = min(goal_drop/max(goal_max,1.0), 1.0)
                if over_move > 0.3:
                    drop_strength = max(drop_strength, min(over_move/2.0, 0.8))
                if over_decline > 0.002:
                    drop_strength = max(drop_strength, min(over_decline*50, 0.6))
                if goal_vol > 1.5:
                    drop_strength *= 0.5

                if drop_strength > 0:
                    drop_lambda = max(raw_lam - drop_strength * 3.0, raw_lam * 0.5)
                    print(f"  → 向下修正: drop_strength={drop_strength:.3f}, λ={raw_lam:.2f}→{drop_lambda:.2f}")
                else:
                    print(f"  → 无触发条件，不做调整")

        # 模拟市场衰减
        market_h = feats.get("odds_market_home_prob", 0.33)
        market_a = feats.get("odds_market_away_prob", 0.33)
        prob_diff = abs(market_h - 1/3) + abs(market_a - 1/3)
        max_diff = 2*(1-1/3)
        norm_diff = min(prob_diff/max_diff, 1.0)
        max_att, min_att = 0.03, 0.01  # 韩K
        att = max_att - (max_att - min_att) * norm_diff
        print(f"\n市场衰减:")
        print(f"  market_h={market_h:.3f}, market_a={market_a:.3f}")
        print(f"  norm_diff={norm_diff:.3f}, attenuation={att:.4f}")
        print(f"  λ → {raw_lam*(1-att):.4f}")

asyncio.run(main())
