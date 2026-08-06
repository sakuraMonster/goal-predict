"""
韩K联 重预测验证脚本：07-28 ~ 08-03
- 从 Match 表读取比赛和实际比分
- 用 PredictionPipeline 进行预测
- 输出中间过程，统计 SNAP 命中率
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
        # ── 1. 查找韩K联的 league_id ──
        lg_result = await db.execute(
            select(League).where(League.name_zh == "韩K")
        )
        kleague = lg_result.scalar_one_or_none()
        if not kleague:
            print("[ERROR] 未找到联赛 '韩K'")
            return
        print(f"[INFO] 韩K联 league_id={kleague.id}, name_zh={kleague.name_zh}")

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
                    Match.league_id == kleague.id,
                    Match.kickoff_time >= start,
                    Match.kickoff_time < end,
                )
            )
            .order_by(Match.kickoff_time)
        )
        matches = list(match_result.unique().scalars().all())
        print(f"[INFO] 查询到 {len(matches)} 场韩K联比赛")

        if not matches:
            print("[WARN] 无比赛数据")
            return

        pipeline = PredictionPipeline(db)
        feat_b = FeatureEngineerB(db)
        results = []

        print()
        print("=" * 120)
        print("  韩K联 模型B 重预测验证：07-28 ~ 08-03")
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

            # ── 模拟 _apply_goal_market_adjustment 计算 drop_strength/market_weight_rev ──
            # (与 pipeline.py 中完全一致的逻辑)
            drop_strength = 0.0
            market_weight_rev = 0.0

            # 韩K规则: invert=True, large_drop = goal_drop >= 1.0
            invert = True  # 韩K: LEAGUE_MARKET_ADJUSTMENT_WEIGHT = -1.0
            large_drop = goal_drop >= 1.0

            if goal_line_market >= 0.5 and goal_line_max >= 0.5 and large_drop:
                market_lambda = goal_line_market
                lambda_val = raw_lam * (1.0 - new_pred.get("zero_inflation_prob", 0))

                # 向下修正: 计算 drop_strength
                if goal_drop > 0.5:
                    drop_strength = min(goal_drop / max(goal_line_max, 1.0), 1.0)
                if over_move > 0.3:
                    drop_strength = max(drop_strength, min(over_move / 2.0, 0.8))
                if over_decline > 0.002:
                    drop_strength = max(drop_strength, min(over_decline * 50, 0.6))
                if goal_vol > 1.5:
                    drop_strength *= 0.5

                # 向上修正判断（优先）
                if market_lambda >= 2.5 and lambda_val < market_lambda * 0.7:
                    gap_ratio = (market_lambda - lambda_val) / max(market_lambda, 1.0)
                    market_weight = min(0.15 + 0.25 * gap_ratio, 0.40)
                    # 韩K反向：市场诱大球 → 压低λ看小球
                    print(f"    [调整] 向上修正(反向): market_λ={market_lambda:.2f} > model_λ={lambda_val:.2f}*0.7, "
                          f"gap={gap_ratio:.3f}, weight={market_weight:.2f}")

                elif drop_strength > 0.05 and lambda_val > market_lambda:
                    # 韩K反向：市场诱导小球 → goal_drop驱动推高λ看大球
                    market_weight_rev = min(0.42 * drop_strength, 0.50)
                    print(f"    [调整] 向下修正(反向): drop_strength={drop_strength:.3f}, "
                          f"market_weight_rev={market_weight_rev:.3f}, "
                          f"λ_before={lambda_val:.2f}, market_λ={market_lambda:.2f}")

                elif drop_strength > 0.05 and lambda_val < market_lambda:
                    market_weight = min(0.20 * drop_strength, 0.35)
                    # 韩K反向：市场诱大球 → 压低λ看小球
                    print(f"    [调整] 向上拉动(反向): drop_strength={drop_strength:.3f}, "
                          f"market_weight={market_weight:.2f}")
            elif not large_drop:
                print(f"    [跳过] goal_drop={goal_drop:.2f} < 1.0, 韩K反向不触发")
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


if __name__ == "__main__":
    asyncio.run(main())
