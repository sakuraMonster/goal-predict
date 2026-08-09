"""使用更新后的 Model C 对近30天全部比赛重预测"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta, date
from collections import defaultdict
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction, League
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

OUTPUT = os.path.join(os.path.dirname(__file__), "_full_30d_prediction.txt")
JSON_OUT = os.path.join(os.path.dirname(__file__), "_full_30d_prediction.json")

async def main():
    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        result = await db.execute(select(League.id, League.name_zh))
        league_names = {row[0]: row[1] for row in result}

        # 近30天
        today = date.today()
        end_date = today
        start_date = today - timedelta(days=30)

        qs = datetime(start_date.year, start_date.month, start_date.day, 12, 0, 0)
        qe = datetime(end_date.year, end_date.month, end_date.day, 12, 0, 0) + timedelta(days=1)

        print(f"查询范围: {start_date} ~ {end_date}")

        result = await db.execute(
            select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time >= qs, Match.kickoff_time < qe)
            .order_by(Match.kickoff_time)
        )
        matches = list(result.unique().scalars().all())
        print(f"找到 {len(matches)} 场比赛")

        results = []
        total, failed, skipped = 0, 0, 0

        for m in matches:
            total += 1
            lg_name = league_names.get(m.league_id, "?")
            pred_r = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = pred_r.scalar_one_or_none()
            actual_total = pred.actual_total_goals if pred else None
            actual_score = pred.actual_score if pred else None

            # 旧数据
            old_lam = pred.expected_goals_c if pred else None
            old_snap = pred.snap_top2_c if pred else None
            old_hit = actual_total in old_snap if actual_total is not None and old_snap else None

            try:
                features_df = await feat_engine.extract_features(m.id)
                if features_df.empty:
                    skipped += 1
                    continue
                features = features_df.iloc[0].to_dict()
            except Exception as e:
                failed += 1
                continue

            rc = model_c.predict(features, lg_name)
            d = rc["detail"]
            new_lam = rc["expected_goals"]
            new_snap = snap_top2(new_lam)
            new_hit = actual_total in new_snap if actual_total is not None else None

            # 写回 DB
            if pred:
                pred.expected_goals_c = new_lam
                pred.snap_top2_c = new_snap

            kickoff_str = m.kickoff_time.strftime("%m-%d %H:%M") if m.kickoff_time else "?"
            # 比赛周期: 当日12:00~次日12:00, 标签取起始日期
            cycle_label = (m.kickoff_time - timedelta(hours=12)).strftime("%m-%d") if m.kickoff_time else "?"
            results.append({
                "date": kickoff_str,
                "cycle": cycle_label,
                "home": m.home_team_name or "?",
                "away": m.away_team_name or "?",
                "league": lg_name,
                "actual_score": actual_score,
                "actual_total": actual_total,
                "new_lam": new_lam, "new_snap": new_snap, "new_hit": new_hit,
                "old_lam": old_lam, "old_snap": old_snap, "old_hit": old_hit,
                "drop": d["goal_drop"], "GL": d["goal_line"], "calib": d["calib"],
                "drop_adj": d["drop_adj"],
            })

        await db.commit()
        print(f"处理完成: 成功 {total - failed - skipped}, 跳过 {skipped}, 失败 {failed}")

    # ====== 汇总输出 ======
    settled = [r for r in results if r["actual_total"] is not None]
    unsettled = len(results) - len(settled)
    new_hit = sum(1 for r in settled if r["new_hit"])
    old_hit = sum(1 for r in settled if r["old_hit"])
    changed_better = [r for r in settled if r["new_hit"] and not r["old_hit"]]
    changed_worse = [r for r in settled if not r["new_hit"] and r["old_hit"]]

    lines = []
    lines.append("=" * 75)
    lines.append("Model C 近30天全量重预测结果")
    lines.append("范围: {} ~ {}".format(start_date, end_date))
    lines.append("=" * 75)
    lines.append("总场次: {}  已结算: {}  未结算: {}".format(len(results), len(settled), unsettled))
    lines.append("修改前: {}/{} = {:.1f}%".format(old_hit, len(settled), old_hit/len(settled)*100 if settled else 0))
    lines.append("修改后: {}/{} = {:.1f}%".format(new_hit, len(settled), new_hit/len(settled)*100 if settled else 0))
    lines.append("改善: +{}  恶化: -{}  净改善: {:+d}".format(len(changed_better), len(changed_worse), len(changed_better) - len(changed_worse)))

    # 按比赛周期分组
    lines.append("--- 按比赛周期（12:00~次日12:00） ---")
    by_date = defaultdict(lambda: {"total": 0, "old_hit": 0, "new_hit": 0})
    for r in settled:
        d = r["cycle"]
        by_date[d]["total"] += 1
        if r["old_hit"]: by_date[d]["old_hit"] += 1
        if r["new_hit"]: by_date[d]["new_hit"] += 1
    for d in sorted(by_date.keys()):
        st = by_date[d]
        lines.append("  {}: {}/{} -> {}/{}  ({:.0f}% -> {:.0f}%)".format(
            d, st["old_hit"], st["total"], st["new_hit"], st["total"],
            st["old_hit"]/st["total"]*100, st["new_hit"]/st["total"]*100))

    # 按联赛分组
    lines.append("")
    lines.append("--- 按联赛 ---")
    by_lg = defaultdict(lambda: {"total": 0, "old_hit": 0, "new_hit": 0})
    for r in settled:
        by_lg[r["league"]]["total"] += 1
        if r["old_hit"]: by_lg[r["league"]]["old_hit"] += 1
        if r["new_hit"]: by_lg[r["league"]]["new_hit"] += 1
    for lg in sorted(by_lg.keys(), key=lambda x: by_lg[x]["total"], reverse=True):
        st = by_lg[lg]
        delta = st["new_hit"] - st["old_hit"]
        lines.append("  {}: {}/{} -> {}/{}  ({:.0f}% -> {:.0f}%)  {:+d}".format(
            lg, st["old_hit"], st["total"], st["new_hit"], st["total"],
            st["old_hit"]/st["total"]*100, st["new_hit"]/st["total"]*100,
            delta))

    # 改善/恶化明细
    if changed_better:
        lines.append("")
        lines.append("--- 改善场次 (MISS->HIT, {}场) ---".format(len(changed_better)))
        for r in changed_better:
            lines.append("  {} {} vs {} ({}) | {}球 | lam {:.2f}->{:.2f} SNAP {}->{} | drop={}".format(
                r["date"][:5], r["home"], r["away"], r["league"],
                r["actual_total"], r["old_lam"] or 0, r["new_lam"], r["old_snap"], r["new_snap"], r["drop"]))

    if changed_worse:
        lines.append("")
        lines.append("--- 恶化场次 (HIT->MISS, {}场) ---".format(len(changed_worse)))
        for r in changed_worse:
            lines.append("  {} {} vs {} ({}) | {}球 | lam {:.2f}->{:.2f} SNAP {}->{} | drop={}".format(
                r["date"][:5], r["home"], r["away"], r["league"],
                r["actual_total"], r["old_lam"] or 0, r["new_lam"], r["old_snap"], r["new_snap"], r["drop"]))

    # 写文件
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # JSON
    json_data = {
        "range": "{}~{}".format(start_date, end_date),
        "summary": {
            "total": len(results), "settled": len(settled), "unsettled": unsettled,
            "old_hit": old_hit, "new_hit": new_hit,
            "old_acc": round(old_hit/len(settled)*100, 1) if settled else 0,
            "new_acc": round(new_hit/len(settled)*100, 1) if settled else 0,
            "improved": len(changed_better), "worsened": len(changed_worse),
        },
        "by_date": {d: dict(st) for d, st in by_date.items()},
        "by_league": {lg: dict(st) for lg, st in by_lg.items()},
        "improved": [{
            "home": r["home"], "away": r["away"], "league": r["league"],
            "date": r["date"], "total": r["actual_total"],
            "old_lam": r["old_lam"], "new_lam": r["new_lam"],
            "old_snap": r["old_snap"], "new_snap": r["new_snap"],
        } for r in changed_better],
        "worsened": [{
            "home": r["home"], "away": r["away"], "league": r["league"],
            "date": r["date"], "total": r["actual_total"],
            "old_lam": r["old_lam"], "new_lam": r["new_lam"],
            "old_snap": r["old_snap"], "new_snap": r["new_snap"],
        } for r in changed_worse],
    }
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2, default=str)

    print("\n".join(lines))
    print("\nJSON saved to", JSON_OUT)

asyncio.run(main())
