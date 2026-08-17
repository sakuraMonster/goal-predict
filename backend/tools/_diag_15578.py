"""周一001(15578) 复盘诊断:
1. Prediction 全字段 + Match 详情
2. OddsSnapshot 时间线(goal_line/over_odds 演变)
3. 用当前特征复现 λ_c，并模拟"盘口时刻"对 λ_c 的影响
"""
import asyncio
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_c import ModelC
from app.predictor.snap import snap_top2
from app import ou_flags

MID = 15578


async def main():
    print("ou_flags.OU_NEW_MODEL_THRESHOLDS =", ou_flags.OU_NEW_MODEL_THRESHOLDS)
    async with async_session() as db:
        # ── 1. Prediction + Match ──
        r = await db.execute(
            select(Prediction).options(joinedload(Prediction.match).joinedload(Match.league))
            .where(Prediction.match_id == MID)
        )
        p = r.unique().scalar_one_or_none()
        if not p:
            print(f"!! 无 Prediction for match {MID}")
            return
        m = p.match
        print("=" * 90)
        print(f"Match {MID}: {m.match_num} {m.home_team_name} vs {m.away_team_name} | "
              f"{m.league.name_zh} | kickoff={m.kickoff_time} | 实际 {m.home_score}:{m.away_score}")
        print(f"  jc_match_id={m.jc_match_id} sm_fixture={m.sportmonks_fixture_id} handicap={m.handicap_line}")
        print(f"  Prediction.created_at={p.created_at} model_version={p.model_version}")
        for f in ["expected_goals", "expected_goals_c", "expected_goals_d",
                  "snap_top2", "snap_top2_c", "snap_top2_d", "over_2_5_prob",
                  "actual_total_goals", "result_goals", "confidence_level",
                  "is_cold_match", "summary_text"]:
            print(f"  {f} = {getattr(p, f)}")
        print(f"  goal_distribution = {p.goal_distribution}")
        print("=" * 90)

        # ── 2. OddsSnapshot 时间线 ──
        r2 = await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id == MID).order_by(OddsSnapshot.snapshot_time, OddsSnapshot.id)
        )
        snaps = r2.scalars().all()
        print(f"OddsSnapshot 共 {len(snaps)} 条:")
        print(f"{'id':<6}{'time':<20}{'bookmaker':<14}{'hw':>6}{'dr':>6}{'aw':>6}{'hcp_line':>9}{'goal_line':>9}{'over':>6}{'under':>6}{'opening':>8}")
        for s in snaps:
            print(f"{s.id:<6}{s.snapshot_time:%m-%d %H:%M:<14}{str(s.bookmaker or '')[:13]:<14}"
                  f"{s.home_win or 0:>6.2f}{s.draw or 0:>6.2f}{s.away_win or 0:>6.2f}"
                  f"{s.handicap_line or 0:>9.2f}{s.goal_line or 0:>9.2f}{s.over_odds or 0:>6.2f}{s.under_odds or 0:>6.2f}{str(s.is_opening):>8}")
        print("=" * 90)

        # 按 bookmaker 聚合: 每个时刻的众数 goal_line / 平均 over
        if snaps:
            from collections import defaultdict
            by_time = defaultdict(list)
            for s in snaps:
                by_time[s.snapshot_time].append(s)
            print("按快照时刻聚合(该时刻所有庄家):")
            for t in sorted(by_time):
                ss = by_time[t]
                gls = sorted(set(round(s.goal_line, 2) for s in ss if s.goal_line))
                from statistics import median, mean
                over_m = mean([s.over_odds for s in ss if s.over_odds]) if any(s.over_odds for s in ss) else 0
                gl_common = max(set(round(s.goal_line, 2) for s in ss if s.goal_line), key=lambda g: sum(1 for s in ss if s.goal_line == g)) if gls else None
                print(f"  {t:%m-%d %H:%M}: 家数={len(ss)} goal_line集合={gls} 众数={gl_common} over均值={over_m:.2f}")
        print("=" * 90)

        # ── 3. 当前特征 + 复现 λ_c ──
        feat = FeatureEngineerB(db)
        fdf = await feat.extract_features(MID)
        if fdf.empty:
            print("!! features EMPTY")
            return
        f = fdf.iloc[0].to_dict()
        model_c = ModelC()
        rc = model_c.predict(f, "瑞典超")
        print("当前特征(库内最新)复现:")
        for k in ["goal_line_market", "goal_line_max", "goal_line_drop_from_peak",
                  "goal_line_volatility", "over_odds_movement", "odds_drift_over_mean",
                  "odds_drift_consensus", "goal_line_shift", "bookmaker_count",
                  "home_games_played", "away_games_played",
                  "home_goals_avg", "away_goals_avg", "home_gf_avg_6", "away_gf_avg_6",
                  "league_avg_total_goals", "odds_market_home_prob", "odds_market_away_prob"]:
            print(f"    {k} = {f.get(k)}")
        print(f"    → λ_c = {rc['expected_goals']}  top2_c = {snap_top2(rc['expected_goals'])}")
        d = rc["detail"]
        for k in ["goal_line", "calib", "strength_adj", "form_adj", "drop_adj",
                  "lambda_market", "lambda_fundamental", "divergence",
                  "induce_score", "market_confidence", "market_weight", "early_season_applied"]:
            print(f"    detail.{k} = {d.get(k)}")
        print("=" * 90)

        # ── 4. 模拟: 若盘口为 3.0 / 3.25 (中午可能状态) → λ_c? ──
        for gl_sim in [3.0, 3.25, 2.75, 2.5]:
            f2 = dict(f)
            f2["goal_line_market"] = gl_sim
            # 模拟: 中午盘口更高且未回落 → drop 相关信号清零
            f2["goal_line_drop_from_peak"] = 0.0
            f2["goal_line_shift"] = 0.0
            f2["goal_line_volatility"] = 0.1
            f2["odds_drift_over_mean"] = 0.0
            f2["odds_drift_consensus"] = 0.0
            rc2 = model_c.predict(f2, "瑞典超")
            d2 = rc2["detail"]
            print(f"  模拟 GL={gl_sim} (无回落信号): λ_c={rc2['expected_goals']} top2={snap_top2(rc2['expected_goals'])} "
                  f"| λmkt={d2['lambda_market']} λfund={d2['lambda_fundamental']} mw={d2['market_weight']} induce={d2['induce_score']}")


asyncio.run(main())
