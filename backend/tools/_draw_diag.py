"""分析001/003为什么被平滑到平局：特征级定位"""
import asyncio, sys, os, pickle, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.db.database import async_session
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA

async def main():
    async with async_session() as db:
        eng = FeatureEngineer(db)
        model_a = ModelA()

        for mid in [15463, 15465]:
            from sqlalchemy import select
            from app.db.models import Match
            mr = await db.execute(select(Match).where(Match.id == mid))
            m = mr.scalar_one_or_none()
            print(f"\n{'='*70}")
            print(f"  {m.match_num}  {m.home_team_name} vs {m.away_team_name}")
            if m.home_score is not None:
                print(f"  实际: {m.home_score}:{m.away_score}")
            print(f"{'='*70}")

            # 提取特征
            df = await eng.extract_features(mid)
            if df.empty: continue
            row = df.iloc[0]

            # 模型原始预测
            raw = model_a.predict(df)
            print(f"\n  模型原始概率: 主{raw['home_prob']:.3f} 平{raw['draw_prob']:.3f} 客{raw['away_prob']:.3f}")
            
            # 获取特征重要度（从模型）
            if model_a.model_wl and hasattr(model_a.model_wl, 'feature_importances_'):
                importances = model_a.model_wl.feature_importances_
                feature_names = model_a.feature_names if hasattr(model_a, 'feature_names') else None
                
                if feature_names and len(feature_names) == len(importances):
                    # 计算每个特征对平局的贡献
                    # 取特征值乘以重要度作为"平局贡献分数"的近似
                    fi = list(zip(feature_names, importances))
                    fi.sort(key=lambda x: x[1], reverse=True)
                    
                    print(f"\n  Top 20 特征重要度:")
                    for name, imp in fi[:20]:
                        val = row.get(name, 'N/A')
                        val_str = f"{val:.4f}" if isinstance(val, (int, float)) else str(val)
                        print(f"    {name:<35} imp={imp:.4f}  val={val_str}")

            # 分析平局倾向的特征组合
            print(f"\n  平局倾向分析:")
            
            # 1. 是否是杯赛/非联赛
            same_league = row.get('season_match_same_league', 1)
            print(f"    season_match_same_league = {same_league:.0f}  {'⚠ 非同赛事，赛季数据不可靠' if same_league < 0.5 else '✓ 同赛事'}")
            
            # 2. 战力接近度
            wr_diff = abs(row.get('win_rate_diff', 0))
            ppg_diff = abs(row.get('points_per_game_diff', 0))
            print(f"    |win_rate_diff|={wr_diff:.3f}  |ppg_diff|={ppg_diff:.3f}  {'⚠ 战力接近→倾向平局' if wr_diff < 0.2 else ''}")
            
            # 3. 进球能力
            h_g = row.get('home_goals_avg', 0)
            a_g = row.get('away_goals_avg', 0)
            print(f"    home_goals_avg={h_g:.2f}  away_goals_avg={a_g:.2f}  {'⚠ 低进球率→倾向平局' if h_g < 1.5 and a_g < 1.5 else ''}")
            
            # 4. xG差
            xg_d = row.get('xG_diff', 0)
            print(f"    xG_diff={xg_d:.3f}  {'⚠ xG接近→倾向平局' if abs(xg_d) < 0.3 else ''}")
            
            # 5. 赔率差
            odds_init_h = row.get('odds_home_initial', 2)
            odds_init_a = row.get('odds_away_initial', 2)
            odds_ratio = odds_init_h / max(odds_init_a, 0.1)
            print(f"    odds_h/a ratio={odds_ratio:.2f}  {'⚠ 赔率接近→倾向平局' if 0.7 < odds_ratio < 1.4 else ''}")
            
            # 6. 主场/客场胜率
            h_home_wr = row.get('home_home_win_rate', 0)
            a_away_wr = row.get('away_away_win_rate', 0)
            print(f"    home_home_win={h_home_wr:.3f}  away_away_win={a_away_wr:.3f}")
            
            # 7. 赛程疲劳
            rest_diff = row.get('rest_days_diff', 0)
            print(f"    rest_days_diff={rest_diff:.1f}")
            
            # 8. H2H
            h2h_count = row.get('h2h_match_count', 0)
            h2h_hwr = row.get('h2h_home_win_rate', 'N/A')
            print(f"    h2h_count={h2h_count:.0f}  h2h_home_win_rate={h2h_hwr}")

            # 9. 意图特征汇总
            block_h = row.get('bookmaker_block_home', 0)
            block_a = row.get('bookmaker_block_away', 0)
            lure_h = row.get('bookmaker_lure_home', 0)
            lure_a = row.get('bookmaker_lure_away', 0)
            intent = row.get('bookmaker_intent', 0)
            print(f"    intent={intent:+.2f}  block(h/a)={block_h:.0f}/{block_a:.0f}  lure(h/a)={lure_h:.0f}/{lure_a:.0f}")

            # 综合判断
            print(f"\n  平局倾向信号计数:")
            signals = []
            if same_league < 0.5: signals.append("非同赛事")
            if wr_diff < 0.2: signals.append("胜率接近")
            if abs(xg_d) < 0.3: signals.append("xG接近")
            if 0.7 < odds_ratio < 1.4: signals.append("赔率接近")
            if h_g < 1.5 and a_g < 1.5: signals.append("低进球率")
            print(f"    触发: {', '.join(signals) if signals else '无'}")


asyncio.run(main())
