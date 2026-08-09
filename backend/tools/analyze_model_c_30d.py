"""对近30天比赛用 Model C 做回归测试"""
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
    # 近30天: 2026-07-07 ~ 2026-08-06
    end_date = datetime(2026, 8, 7, 12, 0, 0)  # 08-07 12:00 UTC
    start_date = end_date - timedelta(days=30)  # 07-08 12:00 UTC

    all_results = []

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        result = await db.execute(
            select(Match)
            .options(joinedload(Match.league))
            .where(
                Match.kickoff_time >= start_date,
                Match.kickoff_time < end_date,
            )
            .order_by(Match.kickoff_time)
        )
        matches = list(result.unique().scalars().all())
        total = len(matches)
        print(f"近30天共 {total} 场比赛\n")

        skipped = 0
        no_result = 0
        for idx, m in enumerate(matches):
            league_name = m.league.name_zh if m.league else "未知"

            pred_result = await db.execute(
                select(Prediction).where(Prediction.match_id == m.id)
            )
            pred = pred_result.scalar_one_or_none()

            actual_total = pred.actual_total_goals if pred else None
            actual_score = pred.actual_score if pred else None

            if actual_total is None:
                no_result += 1
                continue

            try:
                features_df = await feat_engine.extract_features(m.id)
                if features_df.empty:
                    skipped += 1
                    continue
                features = features_df.iloc[0].to_dict()
            except Exception:
                skipped += 1
                continue

            result_c = model_c.predict(features, league_name)
            expected_goals = result_c["expected_goals"]
            snap = snap_top2(expected_goals)
            detail = result_c["detail"]
            hit = actual_total in snap

            kickoff = m.kickoff_time
            date_str = kickoff.strftime("%m-%d") if hasattr(kickoff, 'strftime') else str(kickoff)[:10]

            all_results.append({
                "date": date_str,
                "match_id": m.id,
                "home": m.home_team_name or "?",
                "away": m.away_team_name or "?",
                "league": league_name,
                "actual_score": actual_score,
                "actual_total": actual_total,
                "expected_goals": expected_goals,
                "snap_top2": snap,
                "hit": hit,
                "detail": detail,
            })

            if (idx + 1) % 50 == 0:
                print(f"  进度: {idx + 1}/{total}")

    # ── 汇总统计 ──
    settled = [r for r in all_results if r["hit"] is not None]
    hit_count = sum(1 for r in settled if r["hit"])
    miss_count = sum(1 for r in settled if not r["hit"])

    print(f"\n{'=' * 80}")
    print(f"回归测试汇总")
    print(f"{'=' * 80}")
    print(f"时间范围: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    print(f"总场次: {len(all_results)}")
    print(f"无结果跳过: {no_result}")
    print(f"特征缺失跳过: {skipped}")
    print(f"已结算: {len(settled)}")
    print(f"命中: {hit_count}")
    print(f"未命中: {miss_count}")
    if settled:
        print(f"准确率: {hit_count / len(settled) * 100:.1f}%")

    # ── 按日期 ──
    print(f"\n--- 按日期 ---")
    date_stats = defaultdict(lambda: {"hit": 0, "total": 0})
    for r in settled:
        date_stats[r["date"]]["total"] += 1
        if r["hit"]:
            date_stats[r["date"]]["hit"] += 1
    for d in sorted(date_stats.keys()):
        st = date_stats[d]
        acc = st["hit"] / st["total"] * 100 if st["total"] else 0
        bar = "#" * int(acc / 5) if acc > 0 else ""
        print(f"  {d}: {st['hit']:>2}/{st['total']:<2} = {acc:5.1f}% {bar}")

    # ── 按联赛 ──
    print(f"\n--- 按联赛 ---")
    league_stats = defaultdict(lambda: {"hit": 0, "total": 0})
    for r in settled:
        league_stats[r["league"]]["total"] += 1
        if r["hit"]:
            league_stats[r["league"]]["hit"] += 1
    for lg, st in sorted(league_stats.items(), key=lambda x: x[1]["total"], reverse=True):
        acc = st["hit"] / st["total"] * 100 if st["total"] else 0
        print(f"  {lg}: {st['hit']}/{st['total']} = {acc:.1f}%")

    # ── 按偏差分布 ──
    print(f"\n--- 偏差分布 ---")
    dev_bins = defaultdict(lambda: {"hit": 0, "total": 0})
    for r in settled:
        dev = abs(r["actual_total"] - r["expected_goals"])
        if dev < 0.5:
            key = "0.0-0.5"
        elif dev < 1.0:
            key = "0.5-1.0"
        elif dev < 1.5:
            key = "1.0-1.5"
        elif dev < 2.0:
            key = "1.5-2.0"
        else:
            key = "2.0+"
        dev_bins[key]["total"] += 1
        if r["hit"]:
            dev_bins[key]["hit"] += 1
    for k in ["0.0-0.5", "0.5-1.0", "1.0-1.5", "1.5-2.0", "2.0+"]:
        st = dev_bins[k]
        if st["total"] > 0:
            acc = st["hit"] / st["total"] * 100
            print(f"  {k}: {st['hit']}/{st['total']} = {acc:.1f}%")

    # ── 按诱导/权重维度统计 ──
    print(f"\n--- 架构维度分析 ---")
    # 高诱导 vs 低诱导
    high_induce = [r for r in settled if r["detail"].get("induce_score", 0) >= 0.3]
    low_induce = [r for r in settled if r["detail"].get("induce_score", 0) < 0.3]
    if high_induce:
        hi_hit = sum(1 for r in high_induce if r["hit"])
        print(f"  高诱导(>=0.3): {hi_hit}/{len(high_induce)} = {hi_hit/len(high_induce)*100:.1f}%")
    if low_induce:
        lo_hit = sum(1 for r in low_induce if r["hit"])
        print(f"  低诱导(<0.3):  {lo_hit}/{len(low_induce)} = {lo_hit/len(low_induce)*100:.1f}%")

    # 高背离 vs 低背离
    high_div = [r for r in settled if abs(r["detail"].get("divergence", 0)) >= 0.75]
    low_div = [r for r in settled if abs(r["detail"].get("divergence", 0)) < 0.75]
    if high_div:
        hd_hit = sum(1 for r in high_div if r["hit"])
        print(f"  高背离(>=0.75): {hd_hit}/{len(high_div)} = {hd_hit/len(high_div)*100:.1f}%")
    if low_div:
        ld_hit = sum(1 for r in low_div if r["hit"])
        print(f"  低背离(<0.75):  {ld_hit}/{len(low_div)} = {ld_hit/len(low_div)*100:.1f}%")

    # 市场权重分布
    mw_bins = defaultdict(lambda: {"hit": 0, "total": 0})
    for r in settled:
        mw = r["detail"].get("market_weight", 0.7)
        if mw < 0.4:
            key = "0.30-0.40"
        elif mw < 0.5:
            key = "0.40-0.50"
        elif mw < 0.6:
            key = "0.50-0.60"
        elif mw < 0.7:
            key = "0.60-0.70"
        elif mw < 0.8:
            key = "0.70-0.80"
        else:
            key = "0.80-0.90"
        mw_bins[key]["total"] += 1
        if r["hit"]:
            mw_bins[key]["hit"] += 1
    for k in ["0.30-0.40", "0.40-0.50", "0.50-0.60", "0.60-0.70", "0.70-0.80", "0.80-0.90"]:
        st = mw_bins[k]
        if st["total"] > 0:
            acc = st["hit"] / st["total"] * 100
            print(f"  market_weight {k}: {st['hit']}/{st['total']} = {acc:.1f}%")

    # ── Top 10 最差偏差 ──
    print(f"\n--- Top 10 偏差最大场次 ---")
    worst = sorted(settled, key=lambda r: abs(r["actual_total"] - r["expected_goals"]), reverse=True)[:10]
    for i, r in enumerate(worst):
        d = r["detail"]
        delta = r["actual_total"] - r["expected_goals"]
        print(f"  #{i+1} [{r['date']}] {r['home']} vs {r['away']} ({r['league']})")
        print(f"      实际 {r['actual_total']}球 | λ={r['expected_goals']:.2f} | 偏差 {delta:+.1f}")
        print(f"      λ_market={d.get('lambda_market','?')} λ_fund={d.get('lambda_fundamental','?')} "
              f"mw={d.get('market_weight','?')} induce={d.get('induce_score','?')} "
              f"div={d.get('divergence','?')} conf={d.get('market_confidence','?')}")

    # ── 保存 JSON ──
    output_path = os.path.join(os.path.dirname(__file__), "model_c_analysis_30d.json")
    summary = {
        "date_range": f"{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}",
        "total": len(all_results),
        "settled": len(settled),
        "hit": hit_count,
        "miss": miss_count,
        "accuracy": round(hit_count / len(settled) * 100, 1) if settled else 0,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": summary,
            "by_date": {d: {"hit": st["hit"], "total": st["total"]} for d, st in date_stats.items()},
            "by_league": {lg: {"hit": st["hit"], "total": st["total"]} for lg, st in league_stats.items()},
            "worst10": [{
                "date": r["date"], "home": r["home"], "away": r["away"], "league": r["league"],
                "actual_total": r["actual_total"], "expected_goals": r["expected_goals"],
                "detail": {k: v for k, v in r["detail"].items() if not isinstance(v, (list, dict))},
            } for r in worst],
        }, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n详细结果已保存至: {output_path}")

if __name__ == "__main__":
    asyncio.run(main())
