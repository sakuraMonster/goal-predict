"""提取异常比赛的原始特征值，定位极端特征"""
import asyncio, json, sys
import numpy as np
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match
from app.predictor.features_b import FeatureEngineerB

async def extract():
    async with async_session() as db:
        # 15480 腓特烈斯塔 vs 桑纳菲尤尔
        eng = FeatureEngineerB(db)
        
        for mid in [15480, 15491, 15479]:  # 2 extreme + 1 normal for comparison
            try:
                df = await eng.extract_features(mid, None)
                if df.empty:
                    print(f"\nID={mid}: 无特征数据")
                    continue
                
                row = df.iloc[0]
                print(f"\n{'='*60}")
                print(f"  ID={mid} 特征分析")
                print(f"{'='*60}")
                
                # 检查异常值（绝对值 > 100）
                extreme_features = []
                for col in df.columns:
                    val = row[col]
                    if isinstance(val, (int, float, np.floating, np.integer)):
                        if abs(float(val)) > 100:
                            extreme_features.append((col, float(val)))
                
                if extreme_features:
                    print(f"\n  ⚠ 极端特征 (>100):")
                    for name, val in sorted(extreme_features, key=lambda x: -abs(x[1])):
                        print(f"    {name}: {val}")
                else:
                    print(f"  ✓ 无极端特征")
                
                # 检查关键进球相关特征
                key_features = [
                    'goal_line_market', 'goal_line_jc_diff', 'goal_line_max', 'goal_line_min',
                    'goal_line_drop_from_peak', 'goal_line_volatility', 'goal_line_change',
                    'home_goals_avg', 'away_goals_avg', 'home_goals_against_avg', 'away_goals_against_avg',
                    'home_xG', 'away_xG', 'home_xGA', 'away_xGA',
                    'odds_home_current', 'odds_draw_current', 'odds_away_current',
                    'odds_market_home_prob', 'odds_market_draw_prob', 'odds_market_away_prob',
                    'home_form_pts_6', 'away_form_pts_6', 'home_gf_avg_6', 'away_gf_avg_6',
                    'home_ga_avg_6', 'away_ga_avg_6',
                ]
                print(f"\n  关键特征值:")
                for kf in key_features:
                    if kf in df.columns:
                        print(f"    {kf}: {row[kf]}")
                
            except Exception as e:
                print(f"\nID={mid} error: {e}")
                import traceback
                traceback.print_exc()

asyncio.run(extract())
