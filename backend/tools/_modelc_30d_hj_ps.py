"""Model C 近30天按联赛命中率 + 荷甲/葡超深度根因分析
1) 复现 API league-accuracy 口径（snap_top2_c, actual cap 4）
2) 对荷甲/葡超逐场：ModelC 重跑完整链路 + 特征明细
3) hit/miss 特征对比 + 基础数据质量检查
输出到 utf-8 文件，避免终端乱码
"""
import asyncio
import json
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction, League, TeamSeasonStats
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

OUT = os.path.join(os.path.dirname(__file__), "modelc_30d_hj_ps_deep.txt")


async def main():
    today = datetime(2026, 8, 10)
    start = datetime(2026, 7, 11, 12, 0, 0)
    end = datetime(2026, 8, 11, 12, 0, 0)

    lines = []
    def p(s=""):
        lines.append(s)

    p("=" * 100)
    p(f"Model C 近30天分析 | 窗口 {start.strftime('%m-%d %H:%M')} ~ {end.strftime('%m-%d %H:%M')} (北京时间)")
    p("=" * 100)

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        # 1) 近30天全部预测（API 口径）
        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        )
        preds = result.unique().scalars().all()

        by_league = defaultdict(lambda: {"total": 0, "settled": 0, "hit": 0, "miss": 0, "actuals": []})
        for pred in preds:
            match = pred.match
            if not match:
                continue
            lg = match.league.name_zh if match.league else "未知联赛"
            by_league[lg]["total"] += 1
            if pred.actual_total_goals is None:
                continue
            by_league[lg]["settled"] += 1
            by_league[lg]["actuals"].append(pred.actual_total_goals)
            snap = pred.snap_top2_c if pred.snap_top2_c else pred.snap_top2
            act = min(pred.actual_total_goals, 4)
            hit = bool(snap) and (act in snap)
            by_league[lg]["hit" if hit else "miss"] += 1

        p("\n【Part 1: 近30天按联赛命中率（线上 snap_top2_c 口径, actual cap=4）】")
        p(f"{'联赛':<10}{'总场':>6}{'结算':>6}{'命中':>6}{'未中':>6}{'准确率':>8}   实际进球分布")
        for lg, st in sorted(by_league.items(), key=lambda x: -x[1]["settled"]):
            acc = st["hit"] / st["settled"] * 100 if st["settled"] else 0
            actuals = st["actuals"]
            dist = {g: actuals.count(g) for g in sorted(set(actuals))}
            p(f"{lg:<10}{st['total']:>6}{st['settled']:>6}{st['hit']:>6}{st['miss']:>6}{acc:>7.1f}%   {dist}")

        # 2) 荷甲/葡超逐场深度拆解
        for tgt in ["荷甲", "葡超"]:
            p("\n" + "=" * 100)
            p(f"【Part 2: {tgt} 逐场深度拆解】")
            p("=" * 100)
            tgt_matches = []
            for pred in preds:
                match = pred.match
                if not match or not match.league:
                    continue
                if match.league.name_zh != tgt:
                    continue
                tgt_matches.append((match, pred))

            # 确认联赛 ID（用于查 baseline 与球队统计）
            league = tgt_matches[0][0].league if tgt_matches else None

            # 联赛基线检查
            baseline = await feat_engine._get_league_baseline(league.id) if league else {}
            p(f"\n联赛 id={league.id if league else '?'} name_zh={tgt} 基线 baseline: {json.dumps(baseline, ensure_ascii=False)}")

            for match, pred in tgt_matches:
                lg = match.league.name_zh
                home_t = match.home_team_name or "?"
                away_t = match.away_team_name or "?"
                kt = match.kickoff_time.strftime("%m-%d %H:%M") if match.kickoff_time else "?"
                actual = pred.actual_total_goals
                online_snap = pred.snap_top2_c if pred.snap_top2_c else pred.snap_top2

                p("\n" + "-" * 90)
                p(f"[{kt}] id={match.id} {home_t} vs {away_t} | 实际={actual}球({pred.actual_score})")
                p(f"  线上: expected_goals_c={pred.expected_goals_c} snap_top2_c={online_snap} result_goals={pred.result_goals}")
                p(f"  线上: expected_goals(b)= {pred.expected_goals} snap_top2(b)={pred.snap_top2}")

                # 重跑 ModelC
                try:
                    features_df = await feat_engine.extract_features(match.id)
                    if features_df.empty:
                        p("  [SKIP] 特征为空")
                        continue
                    features = features_df.iloc[0].to_dict()
                except Exception as e:
                    p(f"  [FAIL] 特征提取失败: {e}")
                    continue

                rc = model_c.predict(features, lg)
                d = rc["detail"]
                exp = rc["expected_goals"]
                snap_now = snap_top2(exp)
                hit_now = (actual in snap_now) if actual is not None else None

                p(f"  重跑 ModelC: λ={exp} SNAP={snap_now} 命中={hit_now} | "
                  f"over_2_5={rc['over_2_5_prob']}")
                p(f"    goal_line={d['goal_line']} calib={d['calib']} strength_adj={d['strength_adj']} "
                  f"form_adj={d['form_adj']} drop_adj={d['drop_adj']}")
                p(f"    λ_market={d['lambda_market']} λ_fund={d['lambda_fundamental']} "
                  f"divergence={d['divergence']} induce={d['induce_score']} "
                  f"mkt_conf={d['market_confidence']} mkt_weight={d['market_weight']}")
                p(f"    early_season={d.get('early_season_applied')} "
                  f"played={d.get('home_games_played')}/{d.get('away_games_played')}")
                p(f"    主攻={d['home_goals_avg']} 客攻={d['away_goals_avg']} "
                  f"主近6={d['home_gf_avg_6']} 客近6={d['away_gf_avg_6']} "
                  f"goal_drop={d['goal_drop']} league_rule={d['league_rule_applied']}")

                # 关键特征
                p(f"    league_avg_total_goals={features.get('league_avg_total_goals')} "
                  f"bookmaker_count={features.get('bookmaker_count')} "
                  f"goal_line_market={features.get('goal_line_market')}")
                p(f"    home_goals_avg={features.get('home_goals_avg')} away_goals_avg={features.get('away_goals_avg')} "
                  f"home_gf_6={features.get('home_gf_avg_6')} away_gf_6={features.get('away_gf_avg_6')}")
                p(f"    home_games_played={features.get('home_games_played')} away_games_played={features.get('away_games_played')} "
                  f"has_h2h={features.get('has_h2h')} h2h_cnt={features.get('h2h_match_count')}")

                # 偏差方向
                if actual is not None:
                    delta = actual - exp
                    dirn = "爆大球↑" if delta > 0.5 else ("闷小球↓" if delta < -0.5 else "边界")
                    p(f"    偏差={delta:+.1f} {dirn}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出已写入: {OUT}")


from sqlalchemy import and_
asyncio.run(main())
