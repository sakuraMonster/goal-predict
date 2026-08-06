"""对 07-25 ~ 07-27 三个比赛日用 Model C 重新预测并分析失败原因"""
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
from app.db.models import Match, Prediction, League
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2


async def main():
    dates = ["2026-07-25", "2026-07-26", "2026-07-27"]
    all_results = []
    all_details = []

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        for day_str in dates:
            d_start = datetime.strptime(day_str, "%Y-%m-%d")
            query_start = d_start.replace(hour=12)
            query_end = (d_start + timedelta(days=1)).replace(hour=12)

            result = await db.execute(
                select(Match)
                .options(joinedload(Match.league))
                .where(
                    Match.kickoff_time >= query_start,
                    Match.kickoff_time < query_end,
                )
                .order_by(Match.kickoff_time)
            )
            matches = list(result.unique().scalars().all())

            for m in matches:
                league_name = m.league.name_zh if m.league else "未知"

                # 获取已有预测记录中的实际结果
                pred_result = await db.execute(
                    select(Prediction).where(Prediction.match_id == m.id)
                )
                pred = pred_result.scalar_one_or_none()

                actual_total = pred.actual_total_goals if pred else None
                actual_score = pred.actual_score if pred else None
                existing_goals_c = pred.expected_goals_c if pred else None
                existing_snap_c = pred.snap_top2_c if pred else None

                # 提取特征并预测
                try:
                    features_df = await feat_engine.extract_features(m.id)
                    if features_df.empty:
                        print(f"[SKIP] match_id={m.id} 无特征数据")
                        continue
                    features = features_df.iloc[0].to_dict()
                except Exception as e:
                    print(f"[FAIL] match_id={m.id} 特征提取失败: {e}")
                    continue

                result_c = model_c.predict(features, league_name)
                expected_goals = result_c["expected_goals"]
                snap = snap_top2(expected_goals)
                detail = result_c["detail"]

                # 判定命中
                hit = None
                if actual_total is not None:
                    hit = actual_total in snap

                all_results.append({
                    "date": day_str,
                    "match_id": m.id,
                    "home": m.home_team_name or "?",
                    "away": m.away_team_name or "?",
                    "league": league_name,
                    "actual_score": actual_score,
                    "actual_total": actual_total,
                    "expected_goals": expected_goals,
                    "snap_top2": snap,
                    "hit": hit,
                    "existing_goals_c": existing_goals_c,
                    "existing_snap_c": existing_snap_c,
                    "detail": detail,
                })

                status = "HIT" if hit else "MISS" if hit is False else "N/A"
                print(f"[{day_str}] {m.home_team_name or '?'} vs {m.away_team_name or '?'} | "
                      f"实际: {actual_score}({actual_total}球) | "
                      f"ModelC λ={expected_goals} SNAP={snap} | {status}")

    # ── 汇总统计 ──
    settled = [r for r in all_results if r["hit"] is not None]
    hit_count = sum(1 for r in settled if r["hit"])
    miss_count = sum(1 for r in settled if not r["hit"])

    print("\n" + "=" * 80)
    print("汇总统计")
    print("=" * 80)
    print(f"总场次: {len(all_results)}")
    print(f"已结算: {len(settled)}")
    print(f"命中: {hit_count}")
    print(f"未命中: {miss_count}")
    print(f"准确率: {hit_count / len(settled) * 100:.1f}%" if settled else "准确率: N/A")

    # ── 按日期 ──
    print("\n--- 按日期 ---")
    for day_str in dates:
        day_matches = [r for r in settled if r["date"] == day_str]
        day_hit = sum(1 for r in day_matches if r["hit"])
        print(f"  {day_str}: {day_hit}/{len(day_matches)} = {day_hit / len(day_matches) * 100:.1f}%" if day_matches else f"  {day_str}: 无数据")

    # ── 按联赛 ──
    print("\n--- 按联赛 ---")
    league_stats = defaultdict(lambda: {"hit": 0, "total": 0})
    for r in settled:
        league_stats[r["league"]]["total"] += 1
        if r["hit"]:
            league_stats[r["league"]]["hit"] += 1
    for lg, st in sorted(league_stats.items(), key=lambda x: x[1]["total"], reverse=True):
        acc = st["hit"] / st["total"] * 100
        print(f"  {lg}: {st['hit']}/{st['total']} = {acc:.1f}%")

    # ── 失败场次详细分析 ──
    print("\n" + "=" * 80)
    print("失败场次详细分析")
    print("=" * 80)

    missed = [r for r in settled if not r["hit"]]
    missed.sort(key=lambda r: abs(r["actual_total"] - r["expected_goals"]), reverse=True)

    for i, r in enumerate(missed):
        d = r["detail"]
        actual = r["actual_total"]
        pred = r["expected_goals"]
        delta = actual - pred

        print(f"\n--- #{i+1} [{r['date']}] {r['home']} vs {r['away']} ({r['league']}) ---")
        print(f"  实际比分: {r['actual_score']} (总进球 {actual})")
        print(f"  ModelC λ: {pred:.2f}, SNAP={r['snap_top2']}")
        print(f"  偏差: {delta:+.1f} 球")
        print(f"  计算明细:")
        print(f"    goal_line(市场盘口) = {d['goal_line']:.2f}")
        print(f"    calib(联赛校准)    = {d['calib']:.4f}")
        print(f"    strength_adj       = {d['strength_adj']:.4f}  (主攻{d['home_goals_avg']:.2f} + 客攻{d['away_goals_avg']:.2f} = {d['home_goals_avg'] + d['away_goals_avg']:.2f})")
        print(f"    form_adj           = {d['form_adj']:.4f}  (主近6场{d['home_gf_avg_6']:.2f} + 客近6场{d['away_gf_avg_6']:.2f})")
        print(f"    drop_adj           = {d['drop_adj']:.4f}  (回落={d['goal_drop']:.2f})")
        print(f"    lambda_raw         = {d['lambda_raw']:.4f}")
        if d.get("low_score_applied"):
            print(f"    *** 低分规则触发! factor={d['low_score_factor']} ***")

        # 分析原因
        reasons = []
        if actual > pred + 0.5:
            # 实际进球远高于预测 - 分析可能原因
            if d["goal_line"] < 2.5:
                reasons.append(f"市场盘口偏低({d['goal_line']})，模型基线偏低")
            if d["drop_adj"] < -0.02:
                reasons.append(f"盘口回落衰减过大(drop={d['goal_drop']})，拉低了预测")
            if d["form_adj"] < -0.02:
                reasons.append(f"近期状态因子为负({d['form_adj']:.4f})，但实际打出大球")
            if d.get("low_score_applied"):
                reasons.append(f"低分盘口规则误触发，额外×{d['low_score_factor']}导致预测偏低")
            if d["strength_adj"] < -0.05:
                reasons.append(f"攻击强度因子偏低({d['strength_adj']:.4f})，两队攻击力被低估")
        elif actual < pred - 0.5:
            if d["goal_line"] > 2.5:
                reasons.append(f"市场盘口偏高({d['goal_line']})，但实际打出小球")
            if d["strength_adj"] > 0.05:
                reasons.append(f"攻击强度因子偏高({d['strength_adj']:.4f})，高估了两队攻击力")
            if d["form_adj"] > 0.05:
                reasons.append(f"近期状态因子偏高({d['form_adj']:.4f})，高估了近期进球能力")
        else:
            # 偏差不大但SNAP没覆盖 - 可能是边界case
            if pred - int(pred) > 0.85 or pred - int(pred) < 0.15:
                reasons.append(f"预测值({pred:.2f})处于SNAP边界区，取整丢失了正确的进球数")
            else:
                reasons.append(f"预测偏差较小({delta:+.1f})，但SNAP Top2({r['snap_top2']})未覆盖实际{actual}球")

        if not reasons:
            reasons.append("预测值接近实际，但SNAP Top2恰好未覆盖（随机性）")

        for reason in reasons:
            print(f"  >> 可能原因: {reason}")

    # ── 命中场次也输出简要 ──
    print("\n" + "=" * 80)
    print("命中场次（供参考）")
    print("=" * 80)
    hit_matches = [r for r in settled if r["hit"]]
    for r in sorted(hit_matches, key=lambda x: x["date"]):
        print(f"  [{r['date']}] {r['home']} vs {r['away']} ({r['league']}) | "
              f"实际 {r['actual_total']}球 | λ={r['expected_goals']:.2f} SNAP={r['snap_top2']}")

    # ── 保存 JSON ──
    output_path = os.path.join(os.path.dirname(__file__), "model_c_analysis_0725_0727.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total": len(all_results),
                "settled": len(settled),
                "hit": hit_count,
                "miss": miss_count,
                "accuracy": round(hit_count / len(settled) * 100, 1) if settled else 0,
            },
            "by_date": {d: {"hit": sum(1 for r in settled if r["date"] == d and r["hit"]),
                            "total": sum(1 for r in settled if r["date"] == d)}
                        for d in dates},
            "by_league": {lg: {"hit": st["hit"], "total": st["total"]}
                         for lg, st in league_stats.items()},
            "missed": [{
                "date": r["date"], "match_id": r["match_id"],
                "home": r["home"], "away": r["away"], "league": r["league"],
                "actual_score": r["actual_score"], "actual_total": r["actual_total"],
                "expected_goals": r["expected_goals"], "snap_top2": r["snap_top2"],
                "detail": {k: v for k, v in r["detail"].items() if not isinstance(v, (list, dict))},
            } for r in missed],
            "hit": [{
                "date": r["date"], "match_id": r["match_id"],
                "home": r["home"], "away": r["away"], "league": r["league"],
                "actual_total": r["actual_total"], "expected_goals": r["expected_goals"],
                "snap_top2": r["snap_top2"],
            } for r in hit_matches],
        }, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n详细结果已保存至: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
