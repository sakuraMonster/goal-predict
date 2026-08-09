"""挪超 5+球大球调控方案深度分析"""
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
            if (m.league.name_zh if m.league else "") != "挪超": continue
            pred_result = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = pred_result.scalar_one_or_none()
            actual_total = pred.actual_total_goals if pred else None
            if actual_total is None: continue
            features_df = await feat_engine.extract_features(m.id)
            if features_df.empty: continue
            features = features_df.iloc[0].to_dict()
            result_c = model_c.predict(features, "挪超")
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
                # Extended features
                "home_ga": features.get("home_goals_against_avg",0),
                "away_ga": features.get("away_goals_against_avg",0),
                "home_gf6": result_c["detail"]["home_gf_avg_6"],
                "away_gf6": result_c["detail"]["away_gf_avg_6"],
                "home_form_pts_6": features.get("home_form_pts_6",0),
                "away_form_pts_6": features.get("away_form_pts_6",0),
                "home_form_trend": features.get("home_form_trend",0),
                "away_form_trend": features.get("away_form_trend",0),
                "goal_line_volatility": features.get("goal_line_volatility",0),
                "odds_dispersity": features.get("odds_dispersity",0),
                "odds_consensus_direction": features.get("odds_consensus_direction",0),
                "handicap_consensus_direction": features.get("handicap_consensus_direction",0),
                "league_avg_total_goals": features.get("league_avg_total_goals",0),
                "total_attack": max(0.5, (features.get("home_goals_avg",0) or 0) + (features.get("away_goals_avg",0) or 0)),
            })

    big5 = [r for r in all_data if r["actual_total"] >= 5]
    normal = [r for r in all_data if 2 <= r["actual_total"] <= 4]
    low = [r for r in all_data if r["actual_total"] <= 1]

    print(f"挪超 30天: {len(all_data)}场 (5+球:{len(big5)}场, 2-4球:{len(normal)}场, 0-1球:{len(low)}场)")
    print(f"5+球占比: {len(big5)/len(all_data)*100:.0f}%\n")

    # ═══════════════════════════════════════════════
    # 1. 5+球 vs 2-4球 全维度对比
    # ═══════════════════════════════════════════════
    print("=" * 80)
    print("【5+球场次 (n={}) vs 2-4球场次 (n={}) 全维度对比】".format(len(big5), len(normal)))
    print("=" * 80)
    
    dims = [
        ("goal_line","GL盘口"),
        ("goal_drop","盘口回落"),
        ("goal_line_volatility","GL波动"),
        ("odds_dispersity","赔率离散"),
        ("home_gf","主队赛季进球"),
        ("away_gf","客队赛季进球"),
        ("home_ga","主队赛季失球"),
        ("away_ga","客队赛季失球"),
        ("home_gf6","主近6进球"),
        ("away_gf6","客近6进球"),
        ("home_form_pts_6","主近6积分"),
        ("away_form_pts_6","客近6积分"),
        ("home_form_trend","主形态趋势"),
        ("away_form_trend","客形态趋势"),
        ("total_attack","两队总攻击"),
        ("strength_adj","攻防调整"),
        ("form_adj","状态调整"),
        ("drop_adj","回落调整"),
        ("induce_score","诱导评分"),
        ("divergence","盘口背离"),
        ("market_weight","市场权重"),
        ("odds_consensus_direction","欧赔共识向"),
        ("handicap_consensus_direction","亚盘共识向"),
        ("lambda_market","预测λmkt"),
        ("lambda_fundamental","预测λfund"),
        ("expected_goals","预测λfinal"),
    ]
    
    for field, label in dims:
        bv = [r[field] or 0 for r in big5]
        nv = [r[field] or 0 for r in normal]
        bm = np.mean(bv) if bv else 0; nm = np.mean(nv) if nv else 0
        diff = bm - nm
        bs = np.std(bv) if len(bv)>1 else 0; ns = np.std(nv) if len(nv)>1 else 0
        pooled = np.sqrt((bs**2 + ns**2)/2 + 0.001)
        signal = diff / pooled
        m = " ***" if abs(signal) > 0.8 else " **" if abs(signal) > 0.5 else " *" if abs(signal) > 0.3 else ""
        print(f"  {label:15s}: 5+球={bm:.3f}±{bs:.3f}  正常={nm:.3f}±{ns:.3f}  diff={diff:+.3f} sig={signal:+.2f}{m}")

    # ═══════════════════════════════════════════════
    # 2. 逐场详列大球比赛
    # ═══════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("【5+球比赛逐场详情】")
    print("=" * 80)
    for r in sorted(big5, key=lambda x: x["date"]):
        print(f"\n  [{r['date']}] {r['home']} vs {r['away']} | {r['actual_score']} ({r['actual_total']}球)")
        print(f"    GL={r['goal_line']:.2f}  λ={r['expected_goals']:.2f}  λmkt={r['lambda_market']:.2f}  λfund={r['lambda_fundamental']:.2f}")
        print(f"    主攻{r['home_gf']:.2f}/失{r['home_ga']:.2f}  客攻{r['away_gf']:.2f}/失{r['away_ga']:.2f}  合计攻击={r['total_attack']:.2f}")
        print(f"    近6: 主{r['home_gf6']:.2f}球{r['home_form_pts_6']:.2f}分  客{r['away_gf6']:.2f}球{r['away_form_pts_6']:.2f}分")
        print(f"    盘口回落={r['goal_drop']:.2f}  波动={r['goal_line_volatility']:.2f}  离散={r['odds_dispersity']:.3f}")
        print(f"    mw={r['market_weight']:.3f}  induce={r['induce_score']:.3f}  div={r['divergence']:+.2f}")

    # ═══════════════════════════════════════════════
    # 3. 寻找复合信号
    # ═══════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("【大球复合信号探索】")
    print("=" * 80)

    # Signal 1: 双方攻击力都强
    both_attack = [r for r in all_data if r["home_gf"] > 1.0 and r["away_gf"] > 1.0]
    both_attack_big = [r for r in both_attack if r["actual_total"] >= 5]
    print(f"  双方攻>1.0: {len(both_attack)}场, 其中5+球={len(both_attack_big)}场 ({len(both_attack_big)/max(len(both_attack),1)*100:.0f}%)")

    # Signal 2: GL moderate (2.0-2.75) + 双方攻击力
    gl_mod = [r for r in all_data if 2.0 <= r["goal_line"] <= 2.75]
    gl_mod_both = [r for r in gl_mod if r["home_gf"] > 1.0 and r["away_gf"] > 0.9]
    gl_mod_both_big = [r for r in gl_mod_both if r["actual_total"] >= 5]
    print(f"  GL 2.0-2.75 + 双方攻>: {len(gl_mod_both)}场, 其中5+球={len(gl_mod_both_big)}场 ({len(gl_mod_both_big)/max(len(gl_mod_both),1)*100:.0f}%)")

    # Signal 3: 客队攻击力强
    away_strong = [r for r in all_data if r["away_gf"] > 1.2]
    away_strong_big = [r for r in away_strong if r["actual_total"] >= 5]
    print(f"  客攻>1.2: {len(away_strong)}场, 其中5+球={len(away_strong_big)}场 ({len(away_strong_big)/max(len(away_strong),1)*100:.0f}%)")

    # Signal 4: divergence为正(市场高估)+GL moderate
    div_pos_mod = [r for r in all_data if r["divergence"] > 0 and 2.0 <= r["goal_line"] <= 2.75]
    div_pos_mod_big = [r for r in div_pos_mod if r["actual_total"] >= 5]
    print(f"  div>0 + GL 2.0-2.75: {len(div_pos_mod)}场, 其中5+球={len(div_pos_mod_big)}场 ({len(div_pos_mod_big)/max(len(div_pos_mod),1)*100:.0f}%)")

    # Signal 5: 主队失球多 + 客队攻击强
    home_leak = [r for r in all_data if r["home_ga"] > 1.3 and r["away_gf"] > 1.0]
    home_leak_big = [r for r in home_leak if r["actual_total"] >= 5]
    print(f"  主失>1.3 + 客攻>1.0: {len(home_leak)}场, 其中5+球={len(home_leak_big)}场 ({len(home_leak_big)/max(len(home_leak),1)*100:.0f}%)")

    # Signal 6: 盘口回落>0.3
    drop_sig = [r for r in all_data if r["goal_drop"] > 0.3]
    drop_sig_big = [r for r in drop_sig if r["actual_total"] >= 5]
    print(f"  回落>0.3: {len(drop_sig)}场, 其中5+球={len(drop_sig_big)}场 ({len(drop_sig_big)/max(len(drop_sig),1)*100:.0f}%)")

    # ═══════════════════════════════════════════════
    # 4. 最佳组合信号
    # ═══════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("【组合信号精度】")
    print("=" * 80)

    combos = [
        ("双方攻>1.0 AND GL 2.0-2.75", lambda r: r["home_gf"]>1.0 and r["away_gf"]>1.0 and 2.0<=r["goal_line"]<=2.75),
        ("双方攻>1.0 AND 主失>1.3", lambda r: r["home_gf"]>1.0 and r["away_gf"]>1.0 and r["home_ga"]>1.3),
        ("客攻>1.2 AND GL 2.0-2.75", lambda r: r["away_gf"]>1.2 and 2.0<=r["goal_line"]<=2.75),
        ("双方攻>1.0 AND 回落>0", lambda r: r["home_gf"]>1.0 and r["away_gf"]>1.0 and r["goal_drop"]>0),
        ("合攻>2.5 AND GL<3.0", lambda r: r["total_attack"]>2.5 and r["goal_line"]<3.0),
        ("主失>1.3 AND 客攻>1.2 AND GL 2.0-2.75", lambda r: r["home_ga"]>1.3 and r["away_gf"]>1.2 and 2.0<=r["goal_line"]<=2.75),
    ]

    for label, fn in combos:
        subset = [r for r in all_data if fn(r)]
        big = [r for r in subset if r["actual_total"]>=5]
        hit = [r for r in subset if r["hit"]]
        if subset:
            print(f"  {label}: {len(subset)}场, 5+球={len(big)}场 ({len(big)/len(subset)*100:.0f}%), "
                  f"当前命中={len(hit)}场 ({len(hit)/len(subset)*100:.0f}%)")
            if big:
                names = [f"{r['home'][:4]}vs{r['away'][:4]}" for r in big]
                print(f"    5+球比赛: {', '.join(names)}")

    # ═══════════════════════════════════════════════
    # 5. 如果对命中组合做大球λ提升
    # ═══════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("【大球调控推演】")
    print("=" * 80)
    
    # Best combo: test "双方攻>1.0 AND GL 2.0-2.75"
    test_fn = lambda r: r["home_gf"]>1.0 and r["away_gf"]>0.9 and 2.0<=r["goal_line"]<=2.75
    for boost in [0.3, 0.5, 0.7]:
        new_hits = 0; lost_hits = 0; gained_big = 0
        for r in all_data:
            if test_fn(r):
                boosted = r["expected_goals"] + boost
            else:
                boosted = r["expected_goals"]
            new_hit = r["actual_total"] in snap_top2(boosted)
            if new_hit and not r["hit"]: 
                gained_big += 1
                new_hits += 1
            elif new_hit and r["hit"]: 
                new_hits += 1
            elif not new_hit and r["hit"]: 
                lost_hits += 1
        net = gained_big - lost_hits
        print(f"  λ+{boost:.1f}: 总命中={new_hits}/{len(all_data)} ({new_hits/len(all_data)*100:.0f}%), "
              f"新救大球={gained_big}, 丢失原HIT={lost_hits}, 净={net:+d}")

asyncio.run(main())
