"""
芬超 重预测验证脚本：07-28 ~ 08-03
- 从 Match 表读取比赛和实际比分
- 用 PredictionPipeline 进行预测
- 输出中间过程，统计 SNAP 命中率
- 芬超特点：LEAGUE_MARKET_ADJUSTMENT_WEIGHT = 1.0 (正常跟随市场，非反转)
             LEAGUE_LAMBDA_CALIBRATION = 0.86
             LEAGUE_MARKET_ATTENUATION = default (0.08, 0.03)
"""
import asyncio
import sys
import os
import math

from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import joinedload
from app.db.database import engine
from app.db.models import Match, League, Prediction
from app.predictor.pipeline import PredictionPipeline
from app.predictor.features_b import FeatureEngineerB


async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 7, 28, 12, 0, 0)
    end = datetime(2026, 8, 4, 12, 0, 0)

    async with sf() as db:
        # ── 1. 查找芬超的 league_id ──
        lg_result = await db.execute(
            select(League).where(League.name_zh.contains("芬超"))
        )
        fenchao_league = lg_result.scalar_one_or_none()
        if not fenchao_league:
            print("[ERROR] 未找到联赛 '芬超'")
            return
        print(f"[INFO] 芬超 league_id={fenchao_league.id}, name_zh={fenchao_league.name_zh}")

        # ── 2. 查询比赛 ──
        match_result = await db.execute(
            select(Match)
            .options(
                joinedload(Match.home_team),
                joinedload(Match.away_team),
                joinedload(Match.league),
            )
            .where(
                and_(
                    Match.league_id == fenchao_league.id,
                    Match.kickoff_time >= start,
                    Match.kickoff_time < end,
                )
            )
            .order_by(Match.kickoff_time)
        )
        matches = list(match_result.unique().scalars().all())
        print(f"[INFO] 查询到 {len(matches)} 场芬超比赛")

        if not matches:
            print("[WARN] 无比赛数据")
            return

        pipeline = PredictionPipeline(db)
        feat_b = FeatureEngineerB(db)
        results = []

        print()
        print("=" * 120)
        print("  芬超 模型B 重预测验证：07-28 ~ 08-03")
        print("  LEAGUE_LAMBDA_CALIBRATION = 0.86")
        print("  LEAGUE_MARKET_ADJUSTMENT_WEIGHT = 1.0 (正常跟随市场)")
        print("  LEAGUE_MARKET_ATTENUATION = default (0.08, 0.03)")
        print("=" * 120)
        print()

        for i, m in enumerate(matches):
            mid = m.id
            home_name = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away_name = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
            lg_name = m.league.name_zh if m.league else "?"
            kt = m.kickoff_time.strftime("%m-%d %H:%M") if m.kickoff_time else "?"

            # 实际比分：优先从 Prediction 表获取，回退到 Match 表
            pred_result = await db.execute(
                select(Prediction).where(Prediction.match_id == mid)
            )
            pred = pred_result.scalar_one_or_none()
            if pred and pred.actual_home_score is not None and pred.actual_away_score is not None:
                actual_h = pred.actual_home_score
                actual_a = pred.actual_away_score
            elif m.home_score is not None and m.away_score is not None:
                actual_h = m.home_score
                actual_a = m.away_score
            else:
                actual_h = 0
                actual_a = 0
            actual_total = actual_h + actual_a

            print(f"  [{i+1}] MID={mid} {kt} {home_name} vs {away_name}")

            # ── 提取特征（用于展示 goal_line 等中间值） ──
            try:
                features_b_df = await feat_b.extract_features(mid, None)
                if not features_b_df.empty:
                    f = features_b_df.iloc[0].to_dict()
                else:
                    f = {}
            except Exception as e:
                print(f"    [WARN] 特征提取失败: {type(e).__name__}: {e}")
                f = {}

            # ── 使用 Pipeline 预测 ──
            try:
                new_pred = await pipeline.predict(mid)
            except Exception as e:
                print(f"    [FAIL] 预测失败: {type(e).__name__}: {e}")
                continue

            raw_lam = new_pred.get("raw_lambda", 0)
            adj_lam = new_pred.get("expected_goals", 0)

            # ── 提取盘口特征 ──
            goal_line_market = f.get("goal_line_market", 0) or 0
            goal_drop = f.get("goal_line_drop_from_peak", 0) or 0
            goal_line_max = f.get("goal_line_max", 0) or 0
            goal_vol = f.get("goal_line_volatility", 0) or 0
            over_move = f.get("over_odds_movement", 0) or 0
            over_decline = f.get("over_odds_decline_rate", 0) or 0

            # ── 模拟 _apply_goal_market_adjustment ──
            # 芬超: invert=False, 正常跟随市场（非反转）
            drop_strength = 0.0
            market_weight_rev = 0.0

            # 芬超没有 goal_drop 硬阈值（不像韩K的 >= 1.0）
            # 只要盘口数据有效，就进入调整流程
            if goal_line_market >= 0.5 and goal_line_max >= 0.5:
                market_lambda = goal_line_market
                lambda_val = raw_lam * (1.0 - new_pred.get("zero_inflation_prob", 0))

                # ── 向上修正判断（优先）: 模型严重低于市场 ──
                if market_lambda >= 2.5 and lambda_val < market_lambda * 0.7:
                    gap_ratio = (market_lambda - lambda_val) / max(market_lambda, 1.0)
                    market_weight = min(0.15 + 0.25 * gap_ratio, 0.40)
                    print(f"    [调整] 向上修正: market_λ={market_lambda:.2f} > model_λ={lambda_val:.2f}*0.7, "
                          f"gap={gap_ratio:.3f}, weight={market_weight:.2f} (拉高λ趋近盘口)")
                else:
                    # ── 向下修正: 计算 drop_strength ──
                    if goal_drop > 0.5:
                        drop_strength = min(goal_drop / max(goal_line_max, 1.0), 1.0)
                    if over_move > 0.3:
                        drop_strength = max(drop_strength, min(over_move / 2.0, 0.8))
                    if over_decline > 0.002:
                        drop_strength = max(drop_strength, min(over_decline * 50, 0.6))
                    if goal_vol > 1.5:
                        drop_strength *= 0.5

                    if drop_strength > 0.05 and lambda_val > market_lambda:
                        # 盘口信号看小球 + 模型预测过高 → 向下拉动（正常跟随）
                        market_weight = min(0.30 * drop_strength, 0.50)
                        print(f"    [调整] 向下修正: drop_strength={drop_strength:.3f}, "
                              f"market_weight={market_weight:.3f}, "
                              f"λ={lambda_val:.2f}→market_λ={market_lambda:.2f} (看小球)")
                    elif drop_strength > 0.05 and lambda_val < market_lambda:
                        # 盘口信号看大球 + 模型预测过低 → 向上拉动（正常跟随）
                        market_weight = min(0.20 * drop_strength, 0.35)
                        print(f"    [调整] 向上拉动: drop_strength={drop_strength:.3f}, "
                              f"market_weight={market_weight:.2f}, "
                              f"λ={lambda_val:.2f}→market_λ={market_lambda:.2f} (看大球)")
                    elif drop_strength > 0:
                        # 有回落但条件不满足
                        print(f"    [无调整] drop_strength={drop_strength:.3f}, "
                              f"λ={lambda_val:.2f} vs market_λ={market_lambda:.2f}")
            else:
                print(f"    [跳过] 无有效盘口数据: goal_line={goal_line_market}, goal_max={goal_line_max}")

            # ── SNAP Top2 计算 ──
            snap_top2_str = ""
            snap_hit = False
            effective_lambda = adj_lam

            if adj_lam > 0:
                eg_clean = round(adj_lam, 10)
                frac = eg_clean - math.floor(eg_clean)
                if frac < 0.10:
                    eff = math.floor(eg_clean)
                elif frac > 0.90:
                    eff = math.ceil(eg_clean)
                else:
                    eff = adj_lam
                dists = sorted([(abs(eff - k), k) for k in range(5)])
                top2 = sorted([dists[0][1], dists[1][1]])
                act = min(actual_total, 4)
                snap_hit = act in top2
                snap_top2_str = f"{top2[0]}/{top2[1]}"
                effective_lambda = eff

            # ── 输出 ──
            print(f"    raw_λ          = {raw_lam:.4f}")
            print(f"    goal_drop       = {goal_drop:.4f}")
            print(f"    goal_line_market= {goal_line_market:.4f}")
            print(f"    goal_line_max   = {goal_line_max:.4f}")
            print(f"    drop_strength   = {drop_strength:.4f}")
            print(f"    market_weight_rev= {market_weight_rev:.4f}")
            print(f"    调整后λ         = {adj_lam:.4f}")
            print(f"    SNAP Top2       = {snap_top2_str}")
            print(f"    实际总进球数     = {actual_total} ({actual_h}:{actual_a})")
            print(f"    是否命中         = {'✅ 命中' if snap_hit else '❌ 未命中'}")
            print()

            results.append({
                "id": mid,
                "home": home_name,
                "away": away_name,
                "kickoff": kt,
                "raw_lambda": raw_lam,
                "goal_drop": goal_drop,
                "goal_line_market": goal_line_market,
                "goal_line_max": goal_line_max,
                "drop_strength": drop_strength,
                "market_weight_rev": market_weight_rev,
                "expected_goals": adj_lam,
                "snap_top2": snap_top2_str,
                "snap_hit": snap_hit,
                "actual_total": actual_total,
                "actual_h": actual_h,
                "actual_a": actual_a,
            })

        # ── 汇总 ──
        total = len(results)
        if total == 0:
            print("\n[WARN] 无预测结果")
            return

        snap_hits = sum(1 for r in results if r["snap_hit"])

        print()
        print("=" * 80)
        print(f"  汇总 (共 {total} 场)")
        print("=" * 80)
        print(f"  SNAP Top2 命中率: {snap_hits}/{total} = {snap_hits/total*100:.1f}%")

        # 逐场汇总表
        print()
        print(f"  {'#':<3} {'日期':<11} {'主队':<12} {'客队':<12} {'raw_λ':<8} {'调整λ':<8} {'进球':<4} {'SNAP':<6} {'命中':<4}")
        print("  " + "-" * 78)
        for i, r in enumerate(results):
            H = "V" if r["snap_hit"] else "X"
            print(f"  {i+1:<3} {r['kickoff']:<11} {r['home']:<12} {r['away']:<12} "
                  f"{r['raw_lambda']:<8.2f} {r['expected_goals']:<8.2f} "
                  f"{r['actual_total']:<4} {r['snap_top2']:<6} {H:<4}")

        # ── 未命中深入分析 ──
        missed = [r for r in results if not r["snap_hit"]]
        if missed:
            print()
            print("=" * 100)
            print(f"  未命中深度分析 ({len(missed)} 场)")
            print("=" * 100)

            # 分类：raw_λ偏差、盘口调整方向错误、SNAP覆盖不到
            raw_lambda_bias = []       # raw_λ 距实际进球差值 > 1.5
            market_direction_err = []  # 盘口调整后反而更偏离
            snap_coverage = []         # SNAP 覆盖不到（极端比分）

            for r in missed:
                raw_error = abs(r["raw_lambda"] - r["actual_total"])
                adj_error = abs(r["expected_goals"] - r["actual_total"])
                actual = r["actual_total"]

                # 分类逻辑
                reasons = []
                
                # 1. raw_λ 本身偏差大
                if raw_error > 1.5:
                    reasons.append("raw_λ偏差")
                    raw_lambda_bias.append(r)
                
                # 2. 盘口调整方向错误（调整后比调整前更差）
                if abs(r["expected_goals"] - r["raw_lambda"]) > 0.1:
                    if adj_error > raw_error:
                        reasons.append("盘口调整方向错误")
                        market_direction_err.append(r)
                    elif adj_error <= raw_error:
                        # 盘口调整有改善但仍未命中
                        if not reasons:
                            reasons.append("盘口调整改善但不足")
                
                # 3. SNAP 覆盖不到
                if actual >= 4 and (r["expected_goals"] < 3.0 or r["expected_goals"] > 4.5):
                    reasons.append("极端比分SNAP覆盖不到")
                    snap_coverage.append(r)
                elif actual >= 4 and r["expected_goals"] >= 3.0 and r["expected_goals"] <= 4.5:
                    # λ在3-4.5区间但SNAP top2没覆盖4球 → 离散化边界问题
                    reasons.append("SNAP离散化边界")
                
                if not reasons:
                    reasons.append("综合偏差(SNAP离散化)")
                
                print()
                print(f"  [{r['id']}] {r['kickoff']} {r['home']} vs {r['away']}: {actual}球 ({r['actual_h']}:{r['actual_a']})")
                print(f"    raw_λ={r['raw_lambda']:.2f} → 调整λ={r['expected_goals']:.2f}")
                print(f"    goal_drop={r['goal_drop']:.3f}  goal_line={r['goal_line_market']:.2f}  SNAP={r['snap_top2']}")
                print(f"    raw_error={raw_error:.2f}  adj_error={adj_error:.2f}")
                print(f"    分类: {', '.join(reasons)}")

            # 汇总统计
            print()
            print("  ── 分类汇总 ──")
            print(f"    raw_λ 偏差导致 (|raw_λ-实际| > 1.5):                  {len(raw_lambda_bias)} 场")
            print(f"    盘口调整方向错误 (调整后误差增大):                    {len(market_direction_err)} 场")
            print(f"    极端比分 SNAP 覆盖不到:                              {len(snap_coverage)} 场")

            # ── 芬超针对性改进建议 ──
            print()
            print("=" * 100)
            print("  芬超 inference-layer 改进建议（不涉及重训练）")
            print("=" * 100)
            print()

            # 统计芬超特征
            avg_raw = sum(r["raw_lambda"] for r in results) / total
            avg_adj = sum(r["expected_goals"] for r in results) / total
            avg_actual = sum(r["actual_total"] for r in results) / total
            avg_drop = sum(r["goal_drop"] for r in results) / total
            
            raw_bias_rate = len(raw_lambda_bias) / len(missed) * 100 if missed else 0
            market_err_rate = len(market_direction_err) / len(missed) * 100 if missed else 0
            snap_cov_rate = len(snap_coverage) / len(missed) * 100 if missed else 0

            print(f"  芬超数据概况:")
            print(f"    平均 raw_λ: {avg_raw:.2f}")
            print(f"    平均 调整λ: {avg_adj:.2f}")
            print(f"    平均 实际进球: {avg_actual:.2f}")
            print(f"    平均 goal_drop: {avg_drop:.3f}")
            print(f"    raw_λ → 调整λ 偏差: {avg_adj - avg_raw:+.2f}")
            print()

            print(f"  1. raw_λ 系统性偏差问题 ({len(raw_lambda_bias)}/{len(missed)} = {raw_bias_rate:.0f}%)")
            if avg_raw > avg_actual + 0.3:
                print(f"     诊断: 芬超 raw_λ 均值 {avg_raw:.2f} > 实际进球 {avg_actual:.2f}，模型系统性高估")
                print(f"     建议: 当前 LEAGUE_LAMBDA_CALIBRATION=0.86 可能偏保守")
                suggested_calib = avg_actual / avg_raw if avg_raw > 0 else 0.86
                print(f"     建议值: {suggested_calib:.2f} (实际进球/raw_λ = {avg_actual:.2f}/{avg_raw:.2f})")
            elif avg_raw < avg_actual - 0.3:
                print(f"     诊断: 芬超 raw_λ 均值 {avg_raw:.2f} < 实际进球 {avg_actual:.2f}，模型系统性低估")
                suggested_calib = avg_actual / avg_raw if avg_raw > 0 else 0.86
                print(f"     建议值: {suggested_calib:.2f} (实际进球/raw_λ = {avg_actual:.2f}/{avg_raw:.2f})")
            else:
                print(f"     诊断: raw_λ 与实际情况基本一致（偏差 ≤ 0.3），校准系数合理")

            print()
            print(f"  2. 盘口调整方向问题 ({len(market_direction_err)}/{len(missed)} = {market_err_rate:.0f}%)")
            if market_direction_err:
                print(f"     诊断: 芬超盘口信号与模型方向矛盾时，跟随市场反而降低准确率")
                print(f"     建议: 可考虑降低芬超的盘口调整权重（如设为 0.5），减少对市场的依赖")
                print(f"           或增加方向一致性校验：仅当盘口方向与模型同向时才加强调整")
                # 统计哪些方向的调整出错了
                drop_down = [r for r in market_direction_err if r["drop_strength"] > 0 and r["raw_lambda"] > r["goal_line_market"]]
                push_up = [r for r in market_direction_err if r["drop_strength"] > 0 and r["raw_lambda"] < r["goal_line_market"]]
                print(f"           向上调整出错: {len(push_up)} 场, 向下调整出错: {len(drop_down)} 场")
            else:
                print(f"     诊断: 盘口调整无方向性错误（调整方向正确或未触发调整）")

            print()
            print(f"  3. SNAP 覆盖问题 ({len(snap_coverage)}/{len(missed)} = {snap_cov_rate:.0f}%)")
            if snap_coverage:
                print(f"     诊断: 芬超存在极端比分（≥4球）但模型λ未能反映")
                print(f"     建议: 对于芬超，可考虑扩大 SNAP Top2 → Top3（命中阈值更宽松）")
                print(f"           或在 λ 较高（>3.0）时自动将 {4} 纳入候选（因为 SNAP 天然对 4+ 覆盖弱）")
            else:
                print(f"     诊断: SNAP 覆盖问题不突出")
            
            # 补充：芬超盘口数据质量
            no_market_count = sum(1 for r in results if r["goal_line_market"] < 0.5)
            print()
            print(f"  4. 盘口数据质量")
            print(f"     无有效盘口数据的场次: {no_market_count}/{total}")
            if no_market_count > total * 0.3:
                print(f"     诊断: 芬超盘口数据覆盖率偏低，依赖纯模型预测的比例过高")
                print(f"     建议: 改善芬超盘口数据采集，或对无盘口场次提高校准系数")

            print()
            print(f"  综合建议优先级:")
            suggestions = []
            if raw_bias_rate > 40:
                suggestions.append(("高", "调整 LEAGUE_LAMBDA_CALIBRATION 至建议值"))
            if market_err_rate > 30:
                suggestions.append(("高", "降低芬超盘口调整权重或增加方向校验"))
            if snap_cov_rate > 25:
                suggestions.append(("中", "扩大 SNAP 窗口或纳入 4+ 候选"))
            if no_market_count > total * 0.3:
                suggestions.append(("中", "改善芬超盘口数据采集"))
            suggestions.append(("低", "持续监控芬超数据特征变化"))
            
            for pri, sug in suggestions:
                print(f"    [{pri}] {sug}")


if __name__ == "__main__":
    asyncio.run(main())
