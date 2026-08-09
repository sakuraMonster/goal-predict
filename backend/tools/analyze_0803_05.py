"""08-03~08-05 比赛周期深度分析"""
import asyncio, sys, os, numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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
    all_data = []

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()
        result = await db.execute(
            select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time >= start_date, Match.kickoff_time < end_date)
            .order_by(Match.kickoff_time))
        matches = list(result.unique().scalars().all())

        for m in matches:
            # 比赛周期标签
            cycle = (m.kickoff_time - timedelta(hours=12)).strftime("%m-%d") if hasattr(m.kickoff_time, 'strftime') else "?"
            if cycle not in ("08-03", "08-04", "08-05"):
                continue

            league_name = m.league.name_zh if m.league else "未知"
            pred_result = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = pred_result.scalar_one_or_none()
            actual_total = pred.actual_total_goals if pred else None
            if actual_total is None: continue
            features_df = await feat_engine.extract_features(m.id)
            if features_df.empty: continue
            features = features_df.iloc[0].to_dict()
            result_c = model_c.predict(features, league_name)
            all_data.append({
                "cycle": cycle,
                "home": m.home_team_name or "?", "away": m.away_team_name or "?",
                "actual_total": actual_total, "actual_score": pred.actual_score,
                "expected_goals": result_c["expected_goals"],
                "hit": actual_total in snap_top2(result_c["expected_goals"]),
                "league": league_name,
                "goal_line": result_c["detail"]["goal_line"],
                "lambda_market": result_c["detail"]["lambda_market"],
                "lambda_fundamental": result_c["detail"]["lambda_fundamental"],
                "market_weight": result_c["detail"]["market_weight"],
                "induce_score": result_c["detail"]["induce_score"],
                "divergence": result_c["detail"]["divergence"],
                "goal_drop": result_c["detail"]["goal_drop"],
                "league_rule": result_c["detail"].get("league_rule_applied"),
                "high_goal_risk": result_c.get("high_goal_risk"),
                "home_gf": result_c["detail"]["home_goals_avg"],
                "away_gf": result_c["detail"]["away_goals_avg"],
                "home_gf6": result_c["detail"]["home_gf_avg_6"],
                "away_gf6": result_c["detail"]["away_gf_avg_6"],
            })

    hits = [r for r in all_data if r["hit"]]
    misses = [r for r in all_data if not r["hit"]]
    print(f"08-03~08-05 总计: {len(all_data)}场, HIT={len(hits)}, MISS={len(misses)}, 准确率={len(hits)/len(all_data)*100:.1f}%\n")

    # 按周期
    for cyc in ["08-03", "08-04", "08-05"]:
        cyc_data = [r for r in all_data if r["cycle"] == cyc]
        cyc_hit = sum(1 for r in cyc_data if r["hit"])
        print(f"=== {cyc} ({cyc_hit}/{len(cyc_data)} = {cyc_hit/max(len(cyc_data),1)*100:.0f}%) ===")
        for r in sorted(cyc_data, key=lambda x: x["actual_total"]):
            delta = r["actual_total"] - r["expected_goals"]
            s = "HIT" if r["hit"] else "MISS"
            risk = f" 大球风险={r['high_goal_risk']['level']}" if r.get('high_goal_risk') else ""
            rule = f" 规则={r['league_rule']}" if r.get('league_rule') else ""
            print(f"  {s} {r['home']} vs {r['away']} ({r['league']}) {r['actual_score']}({r['actual_total']}球) "
                  f"λ={r['expected_goals']:.2f} SNAP={snap_top2(r['expected_goals'])} d={delta:+.1f}{risk}{rule}")
            if not r["hit"]:
                print(f"      GL={r['goal_line']:.2f} mkt={r['lambda_market']:.2f} fund={r['lambda_fundamental']:.2f} "
                      f"mw={r['market_weight']:.2f} induce={r['induce_score']:.2f} div={r['divergence']:+.2f} drop={r['goal_drop']:.2f}")
        print()

    # Miss 根因
    print("=" * 70)
    print("Miss 根因逐场分析")
    print("=" * 70)
    for i, r in enumerate(misses):
        delta = r["actual_total"] - r["expected_goals"]
        print(f"\nMiss #{i+1} [{r['cycle']}] {r['home']} vs {r['away']} ({r['league']})")
        print(f"  实际={r['actual_total']}球 λ={r['expected_goals']:.2f} GL={r['goal_line']:.2f}")
        mkt_err = abs(r["lambda_market"] - r["actual_total"])
        fund_err = abs(r["lambda_fundamental"] - r["actual_total"])
        if mkt_err < fund_err:
            print(f"  市场更准(mkt={r['lambda_market']:.2f} err={mkt_err:.1f} < fund={r['lambda_fundamental']:.2f} err={fund_err:.1f})")
            if r["market_weight"] < 0.6:
                print(f"  → mw={r['market_weight']:.2f}偏低，基本面权重过大拖累了市场准确信号")
            else:
                print(f"  → mw={r['market_weight']:.2f}但仍不够，双方都偏")
        elif fund_err < mkt_err:
            print(f"  基本面更准(fund={r['lambda_fundamental']:.2f} err={fund_err:.1f} < mkt={r['lambda_market']:.2f} err={mkt_err:.1f})")
            print(f"  → mw={r['market_weight']:.2f}过高，市场权重压制了基本面")
        else:
            print(f"  双错(mkt={r['lambda_market']:.2f}, fund={r['lambda_fundamental']:.2f})")
        gap = min(abs(r["actual_total"] - s) for s in snap_top2(r["expected_goals"]))
        print(f"  SNAP差={gap}球 分类:", "极端异常" if gap >= 2 else "SNAP边界" if gap <= 1 else "方向偏差")

    print(f"\n当前: {len(hits)}/{len(all_data)} = {len(hits)/len(all_data)*100:.1f}%")

asyncio.run(main())
