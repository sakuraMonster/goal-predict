"""对近30天 Model C 回归数据做深度专业分析"""
import asyncio
import json
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
from app.predictor.snap import snap_top2, snap_top2_norway


async def main():
    end_date = datetime(2026, 8, 7, 12, 0, 0)
    start_date = end_date - timedelta(days=30)

    all_results = []

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        result = await db.execute(
            select(Match)
            .options(joinedload(Match.league))
            .where(Match.kickoff_time >= start_date, Match.kickoff_time < end_date)
            .order_by(Match.kickoff_time)
        )
        matches = list(result.unique().scalars().all())

        print(f"处理 {len(matches)} 场比赛...")
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
            snap = snap_top2_norway(expected_goals) if league_name == "挪超" else snap_top2(expected_goals)
            detail = result_c["detail"]
            hit = actual_total in snap

            all_results.append({
                "league": league_name,
                "actual_total": actual_total,
                "expected_goals": expected_goals,
                "snap": snap,
                "hit": hit,
                "goal_line": detail["goal_line"],
                "lambda_market": detail["lambda_market"],
                "lambda_fundamental": detail["lambda_fundamental"],
                "divergence": detail["divergence"],
                "induce_score": detail["induce_score"],
                "market_confidence": detail["market_confidence"],
                "market_weight": detail["market_weight"],
                "strength_adj": detail["strength_adj"],
                "form_adj": detail["form_adj"],
                "drop_adj": detail["drop_adj"],
                "goal_drop": detail["goal_drop"],
                "home_goals_avg": detail["home_goals_avg"],
                "away_goals_avg": detail["away_goals_avg"],
                "home": m.home_team_name or "?",
                "away": m.away_team_name or "?",
            })

            if (idx + 1) % 20 == 0:
                print(f"  进度: {idx + 1}/{len(matches)}")

    settled = [r for r in all_results if r["hit"] is not None]
    hit_count = sum(1 for r in settled if r["hit"])
    miss_count = sum(1 for r in settled if not r["hit"])
    print(f"\n总样本: {len(settled)}, 命中: {hit_count}, 未命中: {miss_count}, 准确率: {hit_count/len(settled)*100:.1f}%")

    # ═══════════════════════════════════════════════════
    # 1. 方向性偏差分析：模型是否系统性高估/低估？
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【1. 方向性偏差分析】")
    over_actual = [r for r in settled if r["actual_total"] > r["expected_goals"] + 0.3]
    under_actual = [r for r in settled if r["actual_total"] < r["expected_goals"] - 0.3]
    neutral = [r for r in settled if abs(r["actual_total"] - r["expected_goals"]) <= 0.3]
    
    print(f"  模型低估(实际>预测): {len(over_actual)}场, 命中率={sum(1 for r in over_actual if r['hit'])/max(len(over_actual),1)*100:.1f}%")
    print(f"  模型高估(实际<预测): {len(under_actual)}场, 命中率={sum(1 for r in under_actual if r['hit'])/max(len(under_actual),1)*100:.1f}%")
    print(f"  偏差<=0.3:            {len(neutral)}场, 命中率={sum(1 for r in neutral if r['hit'])/max(len(neutral),1)*100:.1f}%")

    # 全局平均偏差
    all_deltas = [r["actual_total"] - r["expected_goals"] for r in settled]
    mean_delta = np.mean(all_deltas)
    print(f"  全局平均偏差 (实际-预测): {mean_delta:+.3f} 球")
    print(f"  偏差标准差: {np.std(all_deltas):.3f} 球")

    # 按联赛的方向偏差
    print(f"\n  按联赛方向偏差:")
    for lg in sorted(set(r["league"] for r in settled)):
        lg_data = [r for r in settled if r["league"] == lg]
        lg_delta = np.mean([r["actual_total"] - r["expected_goals"] for r in lg_data])
        lg_hit = sum(1 for r in lg_data if r["hit"])
        print(f"    {lg:6s}: 偏差={lg_delta:+.3f}, 命中={lg_hit}/{len(lg_data)}")

    # ═══════════════════════════════════════════════════
    # 2. 市场盘口分层分析
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【2. 市场盘口分层】")
    gl_bins = {
        "<2.0": (0, 2.0),
        "2.0-2.25": (2.0, 2.25),
        "2.5": (2.25, 2.75),
        "2.75-3.0": (2.75, 3.25),
        "3.25-3.5": (3.25, 3.75),
        ">3.5": (3.75, 10),
    }
    for label, (lo, hi) in gl_bins.items():
        subset = [r for r in settled if lo <= r["goal_line"] < hi]
        if subset:
            h = sum(1 for r in subset if r["hit"])
            avg_actual = np.mean([r["actual_total"] for r in subset])
            avg_pred = np.mean([r["expected_goals"] for r in subset])
            print(f"  GL {label:>10s}: {len(subset):>3}场, 命中率={h/len(subset)*100:.1f}%, "
                  f"平均实际={avg_actual:.2f}, 平均预测={avg_pred:.2f}, 偏差={avg_actual-avg_pred:+.2f}")

    # ═══════════════════════════════════════════════════
    # 3. 实际进球分布 vs 预测命中率
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【3. 实际进球数分层命中率】")
    for g in range(0, 8):
        subset = [r for r in settled if r["actual_total"] == g or (g == 7 and r["actual_total"] >= 7)]
        if subset:
            h = sum(1 for r in subset if r["hit"])
            avg_pred = np.mean([r["expected_goals"] for r in subset])
            label = f"{g}" if g < 7 else "7+"
            print(f"  实际{label}球: {len(subset):>3}场, 命中率={h/len(subset)*100:.1f}%, 平均预测λ={avg_pred:.2f}")

    # ═══════════════════════════════════════════════════
    # 4. λ_market vs λ_fundamental 信号一致性
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【4. 市场与基本面信号一致性分析】")
    
    # Both agree on direction vs actual
    agree_up = [r for r in settled if 
                r["lambda_market"] > r["goal_line"] and r["lambda_fundamental"] > r["goal_line"]]
    agree_down = [r for r in settled if 
                  r["lambda_market"] < r["goal_line"] and r["lambda_fundamental"] < r["goal_line"]]
    disagree = [r for r in settled if 
                (r["lambda_market"] > r["goal_line"]) != (r["lambda_fundamental"] > r["goal_line"])]
    
    for label, subset in [("双方向上", agree_up), ("双向向下", agree_down), ("信号分歧", disagree)]:
        if subset:
            h = sum(1 for r in subset if r["hit"])
            print(f"  {label}: {len(subset)}场, 命中率={h/len(subset)*100:.1f}%")

    # ═══════════════════════════════════════════════════
    # 5. 联赛参数校准诊断
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【5. 联赛校准诊断】")
    print(f"  (ratio = avg(actual) / avg(goal_line), 1.0=完美)")
    for lg in sorted(set(r["league"] for r in settled)):
        lg_data = [r for r in settled if r["league"] == lg]
        if len(lg_data) >= 5:
            actual_ratio = np.mean([r["actual_total"] for r in lg_data]) / max(0.5, np.mean([r["goal_line"] for r in lg_data]))
            market_ratio = np.mean([r["lambda_market"] for r in lg_data]) / max(0.5, np.mean([r["goal_line"] for r in lg_data]))
            fund_ratio = np.mean([r["lambda_fundamental"] for r in lg_data]) / max(0.5, np.mean([r["goal_line"] for r in lg_data]))
            final_ratio = np.mean([r["expected_goals"] for r in lg_data]) / max(0.5, np.mean([r["goal_line"] for r in lg_data]))
            h = sum(1 for r in lg_data if r["hit"])
            print(f"  {lg:6s}: {len(lg_data):2d}场 actual/GL={actual_ratio:.2f} market/GL={market_ratio:.2f} "
                  f"fund/GL={fund_ratio:.2f} final/GL={final_ratio:.2f} hit={h}/{len(lg_data)}")

    # ═══════════════════════════════════════════════════
    # 6. 诱导识别效果
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【6. 诱导识别效果分析】")
    
    # goal_drop vs actual outcome
    for drop_range, (lo, hi) in [("无回落(0)", (0, 0.2)), ("轻微(0.2-0.5)", (0.2, 0.5)),
                                   ("中等(0.5-1.0)", (0.5, 1.0)), ("较大(1.0-1.5)", (1.0, 1.5)),
                                   ("大回落(>1.5)", (1.5, 10))]:
        subset = [r for r in settled if lo <= r["goal_drop"] < hi]
        if subset:
            h = sum(1 for r in subset if r["hit"])
            avg_actual = np.mean([r["actual_total"] for r in subset])
            avg_pred = np.mean([r["expected_goals"] for r in subset])
            print(f"  {drop_range:>18s}: {len(subset):>3}场, 命中率={h/len(subset)*100:.1f}%, "
                  f"实际={avg_actual:.2f}, 预测={avg_pred:.2f}")

    # ═══════════════════════════════════════════════════
    # 7. 置信度分层
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【7. 市场可信度分层】")
    for conf_range, (lo, hi) in [("低(<0.4)", (0, 0.4)), ("中(0.4-0.6)", (0.4, 0.6)),
                                   ("高(0.6-0.8)", (0.6, 0.8)), ("很高(>0.8)", (0.8, 1.1))]:
        subset = [r for r in settled if lo <= r["market_confidence"] < hi]
        if subset:
            h = sum(1 for r in subset if r["hit"])
            print(f"  可信度{conf_range}: {len(subset)}场, 命中率={h/len(subset)*100:.1f}%")

    # ═══════════════════════════════════════════════════
    # 8. 最佳预测窗口分析
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【8. λ 预测区间命中率热力图】")
    # Group by expected_goals range
    for eg_range, (lo, hi) in [("0.5-1.5", (0, 1.5)), ("1.5-2.0", (1.5, 2.0)),
                                 ("2.0-2.5", (2.0, 2.5)), ("2.5-3.0", (2.5, 3.0)),
                                 ("3.0-3.5", (3.0, 3.5)), ("3.5-4.0", (3.5, 4.0)),
                                 (">4.0", (4.0, 10))]:
        subset = [r for r in settled if lo <= r["expected_goals"] < hi]
        if subset:
            h = sum(1 for r in subset if r["hit"])
            # Show actual goals distribution
            actual_dist = defaultdict(int)
            for r in subset:
                actual_dist[r["actual_total"]] += 1
            dist_str = ", ".join(f"{g}:{c}" for g, c in sorted(actual_dist.items()))
            print(f"  λ {eg_range:>10s}: {len(subset):>3}场, 命中率={h/len(subset)*100:.1f}%, 实际分布=[{dist_str}]")

    # ═══════════════════════════════════════════════════
    # 9. Miss 分类
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【9. Miss 原因分类】")
    missed = [r for r in settled if not r["hit"]]
    # By miss type
    both_wrong = 0  # market and fundamental both wrong (same direction)
    market_wrong = 0  # market wrong, fundamental right
    fund_wrong = 0  # fundamental wrong, market right
    snap_border = 0  # close but SNAP missed

    for r in missed:
        actual = r["actual_total"]
        mkt_err = abs(r["lambda_market"] - actual)
        fund_err = abs(r["lambda_fundamental"] - actual)
        if min(mkt_err, fund_err) > 1.0:
            both_wrong += 1
        elif mkt_err < fund_err:
            # Market was closer, fundamental pulled it away
            fund_wrong += 1
        elif fund_err < mkt_err:
            # Fundamental was closer, market dominated
            market_wrong += 1
        elif mkt_err < 0.5:
            snap_border += 1
        else:
            both_wrong += 1

    print(f"  双错(市场+基本面都偏离>1球): {both_wrong}/{len(missed)} = {both_wrong/max(len(missed),1)*100:.0f}%")
    print(f"  市场错(基本面更近但市场权重大): {market_wrong}/{len(missed)} = {market_wrong/max(len(missed),1)*100:.0f}%")
    print(f"  基本面错(市场更近但基本面干扰): {fund_wrong}/{len(missed)} = {fund_wrong/max(len(missed),1)*100:.0f}%")
    print(f"  SNAP边界(预测接近但Top2不覆盖): {snap_border}/{len(missed)} = {snap_border/max(len(missed),1)*100:.0f}%")

    # ═══════════════════════════════════════════════════
    # 10. 提升路径推演
    # ═══════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print("【10. 提升路径推演】")
    
    # If we fixed "market_wrong" misses (let fundamental win when it's closer)
    fixable_market = sum(1 for r in missed if abs(r["lambda_fundamental"] - r["actual_total"]) < 
                         abs(r["lambda_market"] - r["actual_total"]) and 
                         abs(r["lambda_fundamental"] - r["actual_total"]) < 1.0)
    print(f"  '市场错但基本面近'可挽救: {fixable_market}场")
    
    # If we could detect both_wrong and reduce confidence
    both_wrong_misses = [r for r in missed if abs(r["lambda_fundamental"] - r["actual_total"]) > 1.0 
                         and abs(r["lambda_market"] - r["actual_total"]) > 1.0]
    print(f"  '双错无可挽救': {len(both_wrong_misses)}场 (极端异常比赛)")
    
    # Theoretical ceiling if we fixed all fixable issues
    theoretical_max_hit = hit_count + fixable_market
    print(f"  理论命中上限(修正市场错误): {theoretical_max_hit}/{len(settled)} = {theoretical_max_hit/len(settled)*100:.1f}%")
    print(f"  当前: {hit_count}/{len(settled)} = {hit_count/len(settled)*100:.1f}%")

if __name__ == "__main__":
    asyncio.run(main())
