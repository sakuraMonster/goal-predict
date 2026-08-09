"""欧冠深度逐场分析"""
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
            if (m.league.name_zh if m.league else "") != "欧冠": continue
            pred_result = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = pred_result.scalar_one_or_none()
            actual_total = pred.actual_total_goals if pred else None
            if actual_total is None: continue
            features_df = await feat_engine.extract_features(m.id)
            if features_df.empty: continue
            features = features_df.iloc[0].to_dict()
            result_c = model_c.predict(features, "欧冠")
            all_data.append({
                "home": m.home_team_name or "?", "away": m.away_team_name or "?",
                "date": m.kickoff_time.strftime("%m-%d") if hasattr(m.kickoff_time,'strftime') else str(m.kickoff_time)[:10],
                "actual_total": actual_total, "actual_score": pred.actual_score,
                "expected_goals": result_c["expected_goals"],
                "hit": actual_total in snap_top2(result_c["expected_goals"]),
                "goal_line": result_c["detail"]["goal_line"],
                "lambda_market": result_c["detail"]["lambda_market"],
                "lambda_fundamental": result_c["detail"]["lambda_fundamental"],
                "market_weight": result_c["detail"]["market_weight"],
                "induce_score": result_c["detail"]["induce_score"],
                "divergence": result_c["detail"]["divergence"],
                "goal_drop": result_c["detail"]["goal_drop"],
                "strength_adj": result_c["detail"]["strength_adj"],
                "form_adj": result_c["detail"]["form_adj"],
                "drop_adj": result_c["detail"]["drop_adj"],
                "home_gf": result_c["detail"]["home_goals_avg"],
                "away_gf": result_c["detail"]["away_goals_avg"],
                "home_ga": features.get("home_goals_against_avg",0),
                "away_ga": features.get("away_goals_against_avg",0),
                "home_gf6": result_c["detail"]["home_gf_avg_6"],
                "away_gf6": result_c["detail"]["away_gf_avg_6"],
                "home_form_pts_6": features.get("home_form_pts_6",0),
                "away_form_pts_6": features.get("away_form_pts_6",0),
                "goal_line_volatility": features.get("goal_line_volatility",0),
                "odds_dispersity": features.get("odds_dispersity",0),
                "league_avg_total_goals": features.get("league_avg_total_goals",0),
                "total_attack": max(0.5, (features.get("home_goals_avg",0) or 0) + (features.get("away_goals_avg",0) or 0)),
            })

    hits = [r for r in all_data if r["hit"]]
    misses = [r for r in all_data if not r["hit"]]
    print(f"欧冠 总场次: {len(all_data)}, HIT: {len(hits)}, MISS: {len(misses)}, 准确率: {len(hits)/len(all_data)*100:.1f}%\n")

    print("=" * 90)
    print("【欧冠 全部逐场分析】")
    print("=" * 90)
    for r in sorted(all_data, key=lambda x: x["date"]):
        delta = r["actual_total"] - r["expected_goals"]
        status = "HIT" if r["hit"] else "MISS"
        snap = snap_top2(r["expected_goals"])
        print(f"\n{status} [{r['date']}] {r['home']} vs {r['away']} | 比分={r['actual_score']}({r['actual_total']}球)")
        print(f"  λ={r['expected_goals']:.2f} SNAP={snap} 偏差={delta:+.1f}")
        print(f"  GL={r['goal_line']:.2f} λmkt={r['lambda_market']:.2f} λfund={r['lambda_fundamental']:.2f} "
              f"mw={r['market_weight']:.3f} induce={r['induce_score']:.3f} div={r['divergence']:+.2f} drop={r['goal_drop']:.2f}")
        print(f"  主攻{r['home_gf']:.2f}/失{r['home_ga']:.2f}/近6进{r['home_gf6']:.2f}/近6分{r['home_form_pts_6']:.2f}")
        print(f"  客攻{r['away_gf']:.2f}/失{r['away_ga']:.2f}/近6进{r['away_gf6']:.2f}/近6分{r['away_form_pts_6']:.2f}")

    # Hit vs Miss对比
    print(f"\n{'=' * 90}")
    print("【欧冠 Hit vs Miss 特征对比】")
    print(f"{'=' * 90}")
    for field, label in [
        ("goal_line","GL"),("goal_drop","回落"),("goal_line_volatility","波动"),
        ("odds_dispersity","离散度"),("home_gf","主攻"),("away_gf","客攻"),
        ("home_ga","主失"),("away_ga","客失"),("home_gf6","主近6进"),("away_gf6","客近6进"),
        ("home_form_pts_6","主近6分"),("away_form_pts_6","客近6分"),
        ("total_attack","合攻"),("induce_score","诱导"),("divergence","背离"),
        ("market_weight","mw"),("lambda_market","λmkt"),("lambda_fundamental","λfund"),
        ("expected_goals","λfinal"),
    ]:
        hv=[r[field] or 0 for r in hits]; mv=[r[field] or 0 for r in misses]
        hm=np.mean(hv) if hv else 0; mm=np.mean(mv) if mv else 0
        diff=mm-hm; m=" <<<" if abs(diff)>0.3 else ""
        print(f"  {label:10s}: HIT={hm:.3f}  MISS={mm:.3f}  diff={diff:+.3f}{m}")

    # Miss根因
    print(f"\n{'=' * 90}")
    print("【Miss 根因分析】")
    print(f"{'=' * 90}")
    for i,r in enumerate(misses):
        delta=r["actual_total"]-r["expected_goals"]
        print(f"\n  Miss #{i+1}: {r['home']} vs {r['away']} | GL={r['goal_line']:.2f} → 实际={r['actual_total']}球")
        mkt_err=abs(r["lambda_market"]-r["actual_total"])
        fund_err=abs(r["lambda_fundamental"]-r["actual_total"])
        if mkt_err<fund_err: print(f"    市场更准(mkt错{mkt_err:.1f}<fund错{fund_err:.1f})")
        elif fund_err<mkt_err: print(f"    基本面更准(fund错{fund_err:.1f}<mkt错{mkt_err:.1f}), mw={r['market_weight']:.2f}")
        else: print(f"    双错(mkt错{mkt_err:.1f}, fund错{fund_err:.1f})")
        gap=min(abs(r["actual_total"]-s) for s in snap_top2(r["expected_goals"]))
        print(f"    距SNAP最近差: {gap}球, λ需调整≈{gap:.1f}方向")
        if abs(delta)<1.0: print(f"    偏差较小({delta:+.1f})，SNAP边界")
        elif delta>0: print(f"    低估({delta:+.1f})")
        else: print(f"    高估({delta:+.1f})")

    print(f"\n当前: {len(hits)}/{len(all_data)} = {len(hits)/len(all_data)*100:.1f}%")

asyncio.run(main())
