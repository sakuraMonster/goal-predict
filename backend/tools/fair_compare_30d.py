"""正确的新旧模型对比：同一场比赛、同一特征，分别跑 旧路径(旧特征+旧阈值) vs 新路径(新特征+新阈值)
不写回 DB。旧特征通过 _old 后缀键替换，旧阈值通过临时关闭 OU_NEW_MODEL_THRESHOLDS 实现。
"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta, date
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models import model_c as mc_mod
from app.predictor.models.model_c import ModelC
from app.predictor.snap import snap_top2

OUTPUT = os.path.join(os.path.dirname(__file__), "_fair_30d_prediction.txt")

# 旧特征替换映射：新key -> 旧key
OLD_FEAT_MAP = {
    "goal_line_market": "goal_line_market_old",
    "goal_line_drop_from_peak": "goal_line_drop_from_peak_old",
    "goal_line_max": "goal_line_max_old",
    "goal_line_min": "goal_line_min_old",
    "goal_line_change": "goal_line_change_old",
}


def build_old_features(f: dict) -> dict:
    """构造旧模型视角特征（用 _old 后缀特征替换主键）"""
    f_old = dict(f)
    for new_k, old_k in OLD_FEAT_MAP.items():
        if old_k in f:
            f_old[new_k] = f[old_k]
    return f_old


async def main():
    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        result = await db.execute(select(Match.league_id, LeagueName).limit(0)) if False else None
        # 联赛名映射
        from app.db.models import League
        r_lg = await db.execute(select(League.id, League.name_zh))
        league_names = {row[0]: row[1] for row in r_lg}

        today = date.today()
        start_date = today - timedelta(days=30)
        qs = datetime(start_date.year, start_date.month, start_date.day, 12, 0, 0)
        qe = datetime(today.year, today.month, today.day, 12, 0, 0) + timedelta(days=1)

        result = await db.execute(
            select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time >= qs, Match.kickoff_time < qe)
            .order_by(Match.kickoff_time)
        )
        matches = list(result.unique().scalars().all())
        print(f"查询范围: {start_date} ~ {today}，找到 {len(matches)} 场比赛")

        results = []
        for m in matches:
            lg_name = league_names.get(m.league_id, "?")
            pred_r = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = pred_r.scalar_one_or_none()
            actual_total = pred.actual_total_goals if pred else None
            actual_score = pred.actual_score if pred else None

            try:
                features_df = await feat_engine.extract_features(m.id)
                if features_df.empty:
                    continue
                features = features_df.iloc[0].to_dict()
            except Exception:
                continue

            # ── 旧路径：旧特征 + 旧阈值 ──
            f_old = build_old_features(features)
            orig_flag = mc_mod.ou_flags.OU_NEW_MODEL_THRESHOLDS
            mc_mod.ou_flags.OU_NEW_MODEL_THRESHOLDS = False
            rc_old = model_c.predict(f_old, lg_name)
            mc_mod.ou_flags.OU_NEW_MODEL_THRESHOLDS = orig_flag

            # ── 新路径：新特征 + 新阈值 ──
            rc_new = model_c.predict(features, lg_name)

            old_lam = rc_old["expected_goals"]
            new_lam = rc_new["expected_goals"]
            old_snap = snap_top2(old_lam)
            new_snap = snap_top2(new_lam)
            old_hit = actual_total in old_snap if actual_total is not None else None
            new_hit = actual_total in new_snap if actual_total is not None else None

            kickoff_str = m.kickoff_time.strftime("%m-%d %H:%M") if m.kickoff_time else "?"
            cycle_label = (m.kickoff_time - timedelta(hours=12)).strftime("%m-%d") if m.kickoff_time else "?"
            results.append({
                "date": kickoff_str, "cycle": cycle_label,
                "home": m.home_team_name or "?", "away": m.away_team_name or "?",
                "league": lg_name, "actual_score": actual_score, "actual_total": actual_total,
                "old_lam": old_lam, "old_snap": old_snap, "old_hit": old_hit,
                "new_lam": new_lam, "new_snap": new_snap, "new_hit": new_hit,
                "gl_new": features.get("goal_line_market", 0),
                "gl_old": features.get("goal_line_market_old", 0),
                "drop_new": features.get("goal_line_drop_from_peak", 0),
                "drop_old": features.get("goal_line_drop_from_peak_old", 0),
            })

    # ====== 汇总 ======
    settled = [r for r in results if r["actual_total"] is not None]
    unsettled = len(results) - len(settled)
    old_hit = sum(1 for r in settled if r["old_hit"])
    new_hit = sum(1 for r in settled if r["new_hit"])
    better = [r for r in settled if r["new_hit"] and not r["old_hit"]]
    worse = [r for r in settled if not r["new_hit"] and r["old_hit"]]

    lines = []
    lines.append("=" * 75)
    lines.append("Model C 公平对比（同特征，旧路径 vs 新路径）")
    lines.append("范围: {} ~ {}".format(start_date, today))
    lines.append("=" * 75)
    lines.append("总场次: {}  已结算: {}  未结算: {}".format(len(results), len(settled), unsettled))
    lines.append("旧路径: {}/{} = {:.1f}%".format(old_hit, len(settled), old_hit/len(settled)*100 if settled else 0))
    lines.append("新路径: {}/{} = {:.1f}%".format(new_hit, len(settled), new_hit/len(settled)*100 if settled else 0))
    lines.append("改善: +{}  恶化: -{}  净改善: {:+d}".format(len(better), len(worse), len(better)-len(worse)))

    lines.append("")
    lines.append("--- 按比赛周期 ---")
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

    lines.append("")
    lines.append("--- 按联赛 ---")
    by_lg = defaultdict(lambda: {"total": 0, "old_hit": 0, "new_hit": 0})
    for r in settled:
        by_lg[r["league"]]["total"] += 1
        if r["old_hit"]: by_lg[r["league"]]["old_hit"] += 1
        if r["new_hit"]: by_lg[r["league"]]["new_hit"] += 1
    for lg in sorted(by_lg.keys(), key=lambda x: by_lg[x]["total"], reverse=True):
        st = by_lg[lg]
        lines.append("  {}: {}/{} -> {}/{}  ({:.0f}% -> {:.0f}%)  {:+d}".format(
            lg, st["old_hit"], st["total"], st["new_hit"], st["total"],
            st["old_hit"]/st["total"]*100, st["new_hit"]/st["total"]*100,
            st["new_hit"] - st["old_hit"]))

    if better:
        lines.append("")
        lines.append("--- 改善场次 (MISS->HIT, {}场) ---".format(len(better)))
        for r in better:
            lines.append("  {} {} vs {} ({}) | {}球 | lam {:.2f}->{:.2f} SNAP {}->{} | GL {}->{} drop {}->{}".format(
                r["date"][:5], r["home"], r["away"], r["league"], r["actual_total"],
                r["old_lam"], r["new_lam"], r["old_snap"], r["new_snap"],
                r["gl_old"], r["gl_new"], r["drop_old"], r["drop_new"]))

    if worse:
        lines.append("")
        lines.append("--- 恶化场次 (HIT->MISS, {}场) ---".format(len(worse)))
        for r in worse:
            lines.append("  {} {} vs {} ({}) | {}球 | lam {:.2f}->{:.2f} SNAP {}->{} | GL {}->{} drop {}->{}".format(
                r["date"][:5], r["home"], r["away"], r["league"], r["actual_total"],
                r["old_lam"], r["new_lam"], r["old_snap"], r["new_snap"],
                r["gl_old"], r["gl_new"], r["drop_old"], r["drop_new"]))

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print("\nSaved to", OUTPUT)


asyncio.run(main())
