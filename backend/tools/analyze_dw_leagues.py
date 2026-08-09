"""深度分析：双错信号挖掘 + 联赛专属规则探索"""
import asyncio
import sys
import os
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2


async def main():
    end_date = datetime(2026, 8, 7, 12, 0, 0)
    start_date = end_date - timedelta(days=30)

    all_matches = []

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        result = await db.execute(
            select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time >= start_date, Match.kickoff_time < end_date)
            .order_by(Match.kickoff_time)
        )
        matches = list(result.unique().scalars().all())

        print(f"提取特征中...")
        for idx, m in enumerate(matches):
            league_name = m.league.name_zh if m.league else "未知"
            pred_result = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = pred_result.scalar_one_or_none()
            actual_total = pred.actual_total_goals if pred else None
            if actual_total is None:
                continue

            try:
                features_df = await feat_engine.extract_features(m.id)
                if features_df.empty:
                    continue
                features = features_df.iloc[0].to_dict()
            except Exception:
                continue

            result_c = model_c.predict(features, league_name)
            expected_goals = result_c["expected_goals"]
            hit = actual_total in snap_top2(expected_goals)

            mkt_err = abs(result_c["detail"]["lambda_market"] - actual_total)
            fund_err = abs(result_c["detail"]["lambda_fundamental"] - actual_total)

            all_matches.append({
                "league": league_name,
                "home": m.home_team_name or "?",
                "away": m.away_team_name or "?",
                "actual_total": actual_total,
                "expected_goals": expected_goals,
                "hit": hit,
                "mkt_err": mkt_err,
                "fund_err": fund_err,
                "is_double_wrong": (mkt_err > 1.0 and fund_err > 1.0),
                "goal_line": result_c["detail"]["goal_line"],
                "lambda_market": result_c["detail"]["lambda_market"],
                "lambda_fundamental": result_c["detail"]["lambda_fundamental"],
                "divergence": result_c["detail"]["divergence"],
                "induce_score": result_c["detail"]["induce_score"],
                "market_confidence": result_c["detail"]["market_confidence"],
                "market_weight": result_c["detail"]["market_weight"],
                "goal_drop": result_c["detail"]["goal_drop"],
                # Raw features for analysis
                "home_goals_avg": features.get("home_goals_avg", 0),
                "away_goals_avg": features.get("away_goals_avg", 0),
                "home_goals_against_avg": features.get("home_goals_against_avg", 0),
                "away_goals_against_avg": features.get("away_goals_against_avg", 0),
                "goal_line_volatility": features.get("goal_line_volatility", 0),
                "odds_dispersity": features.get("odds_dispersity", 0),
                "league_avg_total_goals": features.get("league_avg_total_goals", 0),
                "home_gf_avg_6": features.get("home_gf_avg_6", 0),
                "away_gf_avg_6": features.get("away_gf_avg_6", 0),
                "home_form_pts_6": features.get("home_form_pts_6", 0),
                "away_form_pts_6": features.get("away_form_pts_6", 0),
                "home_win_rate": features.get("home_win_rate", 0),
                "away_win_rate": features.get("away_win_rate", 0),
                "odds_consensus_direction": features.get("odds_consensus_direction", 0),
                "handicap_consensus_direction": features.get("handicap_consensus_direction", 0),
                "fundamental_vs_market_divergence": features.get("fundamental_vs_market_divergence", 0),
            })

            if (idx + 1) % 20 == 0:
                print(f"  进度: {idx + 1}/{len(matches)}")

    print(f"\n总样本: {len(all_matches)}")

    # ═══════════════════════════════════════════════════
    # Part 1: 双错 vs 非双错 特征对比
    # ═══════════════════════════════════════════════════
    dw = [r for r in all_matches if r["is_double_wrong"]]
    non_dw = [r for r in all_matches if not r["is_double_wrong"]]

    print(f"\n{'='*70}")
    print(f"【Part 1: 双错 (n={len(dw)}) vs 非双错 (n={len(non_dw)}) 信号对比】")
    print(f"{'='*70}")

    # 对比特征维度
    dims = [
        ("goal_line", "市场盘口"),
        ("goal_line_volatility", "盘口波动"),
        ("odds_dispersity", "赔率离散度"),
        ("goal_drop", "盘口回落"),
        ("home_goals_avg", "主队场均进球"),
        ("away_goals_avg", "客队场均进球"),
        ("home_goals_against_avg", "主队场均失球"),
        ("away_goals_against_avg", "客队场均失球"),
        ("home_gf_avg_6", "主近6场进球"),
        ("away_gf_avg_6", "客近6场进球"),
        ("home_form_pts_6", "主近6场积分"),
        ("away_form_pts_6", "客近6场积分"),
        ("home_win_rate", "主胜率"),
        ("away_win_rate", "客胜率"),
        ("league_avg_total_goals", "联赛场均进球"),
        ("odds_consensus_direction", "欧赔共识方向"),
        ("handicap_consensus_direction", "亚盘共识方向"),
        ("fundamental_vs_market_divergence", "基本面市场背离"),
        ("induce_score", "诱导评分"),
        ("market_confidence", "市场可信度"),
        ("divergence", "盘口-基本面背离"),
    ]

    for field, label in dims:
        dw_vals = [r[field] or 0 for r in dw]
        nd_vals = [r[field] or 0 for r in non_dw]
        dw_mean = np.mean(dw_vals) if dw_vals else 0
        nd_mean = np.mean(nd_vals) if nd_vals else 0
        dw_std = np.std(dw_vals) if len(dw_vals) > 1 else 0
        nd_std = np.std(nd_vals) if len(nd_vals) > 1 else 0
        # Signal strength: difference / pooled_std
        pooled_std = np.sqrt((dw_std**2 + nd_std**2) / 2 + 0.001)
        signal = (dw_mean - nd_mean) / pooled_std

        marker = ""
        if abs(signal) > 0.5:
            marker = " ***" if abs(signal) > 0.8 else " **"
        if abs(signal) > 0.3:
            print(f"  {label:20s}: 双错={dw_mean:.3f}±{dw_std:.3f}  非双错={nd_mean:.3f}±{nd_std:.3f}  "
                  f"信号={signal:+.3f}{marker}")

    # ═══════════════════════════════════════════════════
    # Part 2: 双错的 "方向" 规律
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"【Part 2: 双错比赛的方向规律】")
    dw_over = [r for r in dw if r["actual_total"] > r["expected_goals"] + 0.5]  # 实际远大于预测
    dw_under = [r for r in dw if r["actual_total"] < r["expected_goals"] - 0.5]  # 实际远小于预测

    print(f"  双错中'爆大球': {len(dw_over)}/{len(dw)}")
    print(f"  双错中'闷小球': {len(dw_under)}/{len(dw)}")

    if dw_over:
        print(f"\n  爆大球场次特征 (n={len(dw_over)}):")
        print(f"    平均盘口: {np.mean([r['goal_line'] for r in dw_over]):.2f}")
        print(f"    平均实际进球: {np.mean([r['actual_total'] for r in dw_over]):.1f}")
        print(f"    盘口回落均值: {np.mean([r['goal_drop'] for r in dw_over]):.2f}")
        print(f"    联赛分布: {dict(sorted(Counter(r['league'] for r in dw_over).items(), key=lambda x:-x[1]))}")

    if dw_under:
        print(f"\n  闷小球场次特征 (n={len(dw_under)}):")
        print(f"    平均盘口: {np.mean([r['goal_line'] for r in dw_under]):.2f}")
        print(f"    平均实际进球: {np.mean([r['actual_total'] for r in dw_under]):.1f}")
        print(f"    盘口回落均值: {np.mean([r['goal_drop'] for r in dw_under]):.2f}")
        print(f"    联赛分布: {dict(sorted(Counter(r['league'] for r in dw_under).items(), key=lambda x:-x[1]))}")

    # ═══════════════════════════════════════════════════
    # Part 3: 挪超深度分析
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"【Part 3: 挪超深度拆解】")
    ncc = [r for r in all_matches if r["league"] == "挪超"]
    ncc_hit = [r for r in ncc if r["hit"]]
    ncc_miss = [r for r in ncc if not r["hit"]]
    print(f"\n  总场次: {len(ncc)}, 命中: {len(ncc_hit)}, 未命中: {len(ncc_miss)}, 准确率: {len(ncc_hit)/len(ncc)*100:.1f}%")

    # 逐场分析miss
    print(f"\n  Miss 场次详情:")
    for i, r in enumerate(ncc_miss):
        delta = r["actual_total"] - r["expected_goals"]
        direction = "爆大球↑" if delta > 0.5 else "闷小球↓" if delta < -0.5 else "边界"
        print(f"  #{i+1} {r['home']} vs {r['away']}: 实际={r['actual_total']} λ={r['expected_goals']:.2f} "
              f"({direction}, 偏差={delta:+.1f})")
        print(f"       GL={r['goal_line']:.2f} λmkt={r['lambda_market']:.2f} λfund={r['lambda_fundamental']:.2f} "
              f"mw={r['market_weight']:.2f} induce={r['induce_score']:.2f} div={r['divergence']:.2f} "
              f"主攻{r['home_goals_avg']:.2f}/失{r['home_goals_against_avg']:.2f} "
              f"客攻{r['away_goals_avg']:.2f}/失{r['away_goals_against_avg']:.2f}")

    # 挪超 hit vs miss 特征对比
    print(f"\n  挪超 Hit vs Miss 特征对比:")
    for field, label in [("goal_line", "盘口"), ("goal_drop", "回落"),
                          ("home_goals_avg", "主攻"), ("away_goals_avg", "客攻"),
                          ("home_goals_against_avg", "主失"), ("away_goals_against_avg", "客失")]:
        h_vals = [r[field] or 0 for r in ncc_hit]
        m_vals = [r[field] or 0 for r in ncc_miss]
        print(f"    {label}: HIT={np.mean(h_vals):.3f}  MISS={np.mean(m_vals):.3f}")

    # ═══════════════════════════════════════════════════
    # Part 4: 韩K深度分析
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"【Part 4: 韩K深度拆解】")
    kk = [r for r in all_matches if r["league"] == "韩K"]
    kk_hit = [r for r in kk if r["hit"]]
    kk_miss = [r for r in kk if not r["hit"]]
    print(f"\n  总场次: {len(kk)}, 命中: {len(kk_hit)}, 未命中: {len(kk_miss)}, 准确率: {len(kk_hit)/len(kk)*100:.1f}%")

    print(f"\n  Miss 场次详情:")
    for i, r in enumerate(kk_miss):
        delta = r["actual_total"] - r["expected_goals"]
        direction = "爆大球↑" if delta > 0.5 else "闷小球↓" if delta < -0.5 else "边界"
        print(f"  #{i+1} {r['home']} vs {r['away']}: 实际={r['actual_total']} λ={r['expected_goals']:.2f} "
              f"({direction}, 偏差={delta:+.1f})")
        print(f"       GL={r['goal_line']:.2f} λmkt={r['lambda_market']:.2f} λfund={r['lambda_fundamental']:.2f} "
              f"mw={r['market_weight']:.2f} induce={r['induce_score']:.2f} div={r['divergence']:.2f} "
              f"主攻{r['home_goals_avg']:.2f}/失{r['home_goals_against_avg']:.2f} "
              f"客攻{r['away_goals_avg']:.2f}/失{r['away_goals_against_avg']:.2f}")

    print(f"\n  韩K Hit vs Miss 特征对比:")
    for field, label in [("goal_line", "盘口"), ("goal_drop", "回落"),
                          ("home_goals_avg", "主攻"), ("away_goals_avg", "客攻"),
                          ("home_goals_against_avg", "主失"), ("away_goals_against_avg", "客失")]:
        h_vals = [r[field] or 0 for r in kk_hit]
        m_vals = [r[field] or 0 for r in kk_miss]
        print(f"    {label}: HIT={np.mean(h_vals):.3f}  MISS={np.mean(m_vals):.3f}")

    # ═══════════════════════════════════════════════════
    # Part 5: 进球分布特征
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"【Part 5: 联赛进球分布特征】")
    for lg_name, lg_label in [("挪超", "挪超"), ("韩K", "韩K"), ("瑞典超", "瑞典超"), ("美职联", "美职联")]:
        lg_data = [r for r in all_matches if r["league"] == lg_name]
        if lg_data:
            actuals = [r["actual_total"] for r in lg_data]
            gls = [r["goal_line"] for r in lg_data]
            print(f"  {lg_label}: avg_actual={np.mean(actuals):.2f}, avg_GL={np.mean(gls):.2f}, "
                  f"ratio={np.mean(actuals)/max(0.5,np.mean(gls)):.2f}, "
                  f"dist={dict(sorted(Counter(actuals).items()))}")

    # 0球和5+球占比
    for lg_name in ["挪超", "韩K", "瑞典超", "美职联", "芬超", "巴甲"]:
        lg_data = [r for r in all_matches if r["league"] == lg_name]
        if lg_data:
            zero = sum(1 for r in lg_data if r["actual_total"] == 0)
            high = sum(1 for r in lg_data if r["actual_total"] >= 5)
            print(f"  {lg_name}: 0球率={zero/len(lg_data)*100:.0f}%, 5+球率={high/len(lg_data)*100:.0f}%")

from collections import Counter
asyncio.run(main())
