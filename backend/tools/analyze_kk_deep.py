"""韩K深度逐场分析"""
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

    all_kk = []

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        result = await db.execute(
            select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time >= start_date, Match.kickoff_time < end_date)
            .order_by(Match.kickoff_time)
        )
        matches = list(result.unique().scalars().all())

        for m in matches:
            league_name = m.league.name_zh if m.league else "未知"
            if league_name != "韩K":
                continue

            pred_result = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = pred_result.scalar_one_or_none()
            actual_total = pred.actual_total_goals if pred else None
            if actual_total is None:
                continue

            features_df = await feat_engine.extract_features(m.id)
            if features_df.empty:
                continue
            features = features_df.iloc[0].to_dict()

            result_c = model_c.predict(features, league_name)
            expected_goals = result_c["expected_goals"]
            snap = snap_top2(expected_goals)
            hit = actual_total in snap
            detail = result_c["detail"]

            all_kk.append({
                "home": m.home_team_name or "?",
                "away": m.away_team_name or "?",
                "date": m.kickoff_time.strftime("%m-%d") if hasattr(m.kickoff_time, 'strftime') else str(m.kickoff_time)[:10],
                "actual_total": actual_total,
                "actual_score": pred.actual_score,
                "expected_goals": expected_goals,
                "snap": snap,
                "hit": hit,
                "goal_line": detail["goal_line"],
                "lambda_market": detail["lambda_market"],
                "lambda_fundamental": detail["lambda_fundamental"],
                "market_weight": detail["market_weight"],
                "induce_score": detail["induce_score"],
                "divergence": detail["divergence"],
                "goal_drop": detail["goal_drop"],
                "league_rule_applied": detail.get("league_rule_applied"),
                # Attack/defense
                "home_gf": detail["home_goals_avg"],
                "away_gf": detail["away_goals_avg"],
                "strength_adj": detail["strength_adj"],
                "form_adj": detail["form_adj"],
                "home_gf6": detail["home_gf_avg_6"],
                "away_gf6": detail["away_gf_avg_6"],
                # Raw features
                "home_ga": features.get("home_goals_against_avg", 0),
                "away_ga": features.get("away_goals_against_avg", 0),
                "goal_line_volatility": features.get("goal_line_volatility", 0),
                "odds_dispersity": features.get("odds_dispersity", 0),
                "league_avg_total_goals": features.get("league_avg_total_goals", 0),
                "home_form_pts_6": features.get("home_form_pts_6", 0),
                "away_form_pts_6": features.get("away_form_pts_6", 0),
            })

    hits = [r for r in all_kk if r["hit"]]
    misses = [r for r in all_kk if not r["hit"]]

    print(f"韩K 总场次: {len(all_kk)}, HIT: {len(hits)}, MISS: {len(misses)}, "
          f"准确率: {len(hits)/len(all_kk)*100:.1f}%\n")

    # ═══════════════════════════════════════════════════
    # 逐场详情
    # ═══════════════════════════════════════════════════
    print("=" * 90)
    print("【韩K 全部12场逐场分析】")
    print("=" * 90)

    for r in sorted(all_kk, key=lambda x: x["date"]):
        delta = r["actual_total"] - r["expected_goals"]
        status = "HIT" if r["hit"] else "MISS"
        rule = f" [规则:{r['league_rule_applied']}]" if r["league_rule_applied"] else ""

        print(f"\n{status} [{r['date']}] {r['home']} vs {r['away']} | 比分={r['actual_score']}({r['actual_total']}球)")
        print(f"  λ={r['expected_goals']:.2f} SNAP={r['snap']} 偏差={delta:+.1f}{rule}")
        print(f"  GL={r['goal_line']:.2f} λmkt={r['lambda_market']:.2f} λfund={r['lambda_fundamental']:.2f} "
              f"mw={r['market_weight']:.3f} induce={r['induce_score']:.3f} div={r['divergence']:+.2f} "
              f"drop={r['goal_drop']:.2f}")
        print(f"  主攻{r['home_gf']:.2f}/失{r['home_ga']:.2f}/近6进球{r['home_gf6']:.2f}/近6积分{r['home_form_pts_6']:.2f}")
        print(f"  客攻{r['away_gf']:.2f}/失{r['away_ga']:.2f}/近6进球{r['away_gf6']:.2f}/近6积分{r['away_form_pts_6']:.2f}")
        print(f"  联赛均值={r['league_avg_total_goals']:.2f} 波动={r['goal_line_volatility']:.2f} 离散度={r['odds_dispersity']:.3f}")

    # ═══════════════════════════════════════════════════
    # Hit vs Miss 特征对比
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*90}")
    print("【韩K Hit vs Miss 多维特征对比】")
    print(f"{'='*90}")

    dims = [
        ("goal_line", "GL盘口"),
        ("goal_drop", "盘口回落"),
        ("goal_line_volatility", "GL波动"),
        ("odds_dispersity", "赔率离散度"),
        ("home_gf", "主队赛季进球"),
        ("away_gf", "客队赛季进球"),
        ("home_ga", "主队赛季失球"),
        ("away_ga", "客队赛季失球"),
        ("home_gf6", "主近6进球"),
        ("away_gf6", "客近6进球"),
        ("home_form_pts_6", "主近6积分"),
        ("away_form_pts_6", "客近6积分"),
        ("league_avg_total_goals", "联赛场均进球"),
        ("strength_adj", "攻防调整"),
        ("form_adj", "状态调整"),
        ("induce_score", "诱导评分"),
        ("divergence", "盘口背离"),
        ("market_weight", "市场权重"),
        ("lambda_market", "市场预测λ"),
        ("lambda_fundamental", "基本面预测λ"),
        ("expected_goals", "最终λ"),
    ]

    for field, label in dims:
        h_vals = [r[field] or 0 for r in hits]
        m_vals = [r[field] or 0 for r in misses]
        h_mean = np.mean(h_vals) if h_vals else 0
        m_mean = np.mean(m_vals) if m_vals else 0
        diff = m_mean - h_mean
        marker = " <<<" if abs(diff) > 0.3 else ""
        print(f"  {label:15s}: HIT={h_mean:.3f}  MISS={m_mean:.3f}  diff={diff:+.3f}{marker}")

    # ═══════════════════════════════════════════════════
    # Miss 分类分析
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*90}")
    print("【Miss 逐场根因分析】")
    print(f"{'='*90}")

    for i, r in enumerate(misses):
        delta = r["actual_total"] - r["expected_goals"]
        print(f"\n  Miss #{i+1}: {r['home']} vs {r['away']} | GL={r['goal_line']:.2f} → 实际={r['actual_total']}球")

        # Check if misclassification reasons
        reasons = []

        # 1. How far is the actual from SNAP?
        if r["actual_total"] in r["snap"]:
            reasons.append("本应命中但SNAP计算异常")
        else:
            closest_snap = min(r["snap"], key=lambda x: abs(x - r["actual_total"]))
            reasons.append(f"SNAP={r['snap']}, 最近={closest_snap}, 差{abs(r['actual_total']-closest_snap)}球")

        # 2. Market vs fundamental accuracy
        mkt_err = abs(r["lambda_market"] - r["actual_total"])
        fund_err = abs(r["lambda_fundamental"] - r["actual_total"])
        if mkt_err < fund_err:
            reasons.append(f"市场更准(mkt错{mkt_err:.1f}<fund错{fund_err:.1f})，基本面拖累")
        elif fund_err < mkt_err:
            reasons.append(f"基本面更准(fund错{fund_err:.1f}<mkt错{mkt_err:.1f})，市场权重({r['market_weight']:.2f})过高")
        else:
            reasons.append(f"双错(mkt错{mkt_err:.1f}, fund错{fund_err:.1f})")

        # 3. League rule effect
        if r["league_rule_applied"]:
            reasons.append(f"联赛规则已触发: {r['league_rule_applied']}")
        else:
            reasons.append("联赛规则未触发")

        # 4. Goal drop signal
        if r["goal_drop"] > 0.5:
            reasons.append(f"盘口回落{drop:.2f}→市场看小，但实际{'大' if delta>0 else '小'}球")
            if r["away_gf"] > 1.0:
                reasons.append(f"客队攻击力{r['away_gf']:.2f}>1.0，回落+客攻强=爆冷信号（韩K规则应触发）")

        # 5. Directional analysis
        if delta > 1.0:
            reasons.append(f"严重低估({delta:+.1f}球)，盘口GL={r['goal_line']:.2f}但实际高出很多")
        elif delta < -1.0:
            reasons.append(f"严重高估({delta:+.1f}球)")
        else:
            reasons.append(f"偏差较小({delta:+.1f})，属于SNAP边界问题")

        for reason in reasons:
            print(f"    → {reason}")

    # ═══════════════════════════════════════════════════
    # 可挽救性评估
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*90}")
    print("【可挽救性评估】")
    print(f"{'='*90}")

    salvageable = 0
    for r in misses:
        delta = abs(r["actual_total"] - r["expected_goals"])
        # If λ is within 0.8 of actual but SNAP missed
        if delta < 0.8:
            print(f"  △ 可挽救: {r['home']} vs {r['away']} (偏差{delta:.1f}, SNAP={r['snap']}, 实际={r['actual_total']})")
            salvageable += 1
        elif delta < 1.5 and r["league_rule_applied"] is None:
            print(f"  ? 可能可挽救(规则未触发): {r['home']} vs {r['away']} (偏差{delta:.1f})")
        else:
            print(f"  ✗ 不可挽救: {r['home']} vs {r['away']} (偏差{delta:.1f}, 实际={r['actual_total']}, GL={r['goal_line']})")

    print(f"\n  当前命中: {len(hits)}/{len(all_kk)}")
    print(f"  可挽救: {salvageable}场")
    print(f"  潜在命中率: {(len(hits)+salvageable)}/{len(all_kk)} = {(len(hits)+salvageable)/len(all_kk)*100:.1f}%")

asyncio.run(main())
