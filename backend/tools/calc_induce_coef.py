"""计算诱盘各档位需要的最小系数，使失败场次翻盘"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction, League
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

OUTPUT = os.path.join(os.path.dirname(__file__), "_induce_calib.txt")

async def main():
    lines = []
    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()
        result = await db.execute(select(League.id, League.name_zh))
        league_names = {row[0]: row[1] for row in result}

        dates = ["2026-07-25", "2026-07-26", "2026-07-27"]
        induced_miss = []  # drop>1.0 且当前 MISS 的场次

        for day_str in dates:
            d_start = datetime.strptime(day_str, "%Y-%m-%d")
            qs = d_start.replace(hour=12)
            qe = (d_start + timedelta(days=1)).replace(hour=12)
            result = await db.execute(
                select(Match).options(joinedload(Match.league))
                .where(Match.kickoff_time >= qs, Match.kickoff_time < qe)
                .order_by(Match.kickoff_time)
            )
            for m in result.unique().scalars().all():
                lg_name = league_names.get(m.league_id, "?")
                pred_r = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
                pred = pred_r.scalar_one_or_none()
                at = pred.actual_total_goals if pred else None
                if at is None:
                    continue

                features_df = await feat_engine.extract_features(m.id)
                if features_df.empty:
                    continue
                f = features_df.iloc[0].to_dict()
                rc = model_c.predict(f, lg_name)
                d = rc["detail"]
                drop = d["goal_drop"]
                snap = snap_top2(rc["expected_goals"])
                hit = at in snap

                if drop > 1.0 and not hit:
                    induced_miss.append({
                        "home": m.home_team_name, "away": m.away_team_name,
                        "lg": lg_name, "total": at, "drop": drop,
                        "GL": d["goal_line"], "calib": d["calib"],
                        "s": d["strength_adj"], "f": d["form_adj"],
                        "cur_lam": rc["expected_goals"],
                        "low_score": d.get("low_score_applied", False),
                        "ls_factor": d.get("low_score_factor", 1.0),
                        "raw": d["lambda_raw"],
                    })

    lines.append("=" * 70)
    lines.append("诱盘失败场次：drop>1.0 且当前 MISS，计算翻盘所需最小系数")
    lines.append("=" * 70)

    for m in induced_miss:
        drop = m["drop"]
        target_lam = None
        target_snap = None
        actual = m["total"]

        # 找最小 λ 使 SNAP 覆盖 actual
        # SNAP: snap_top2(λ) = [a, b], 需要 actual in [a, b]
        # 逐步遍历 λ 找到最小值
        for lam_candidate in [x/100 for x in range(50, 601)]:  # 0.50 ~ 6.00
            s = snap_top2(lam_candidate)
            if actual in s:
                target_lam = lam_candidate
                target_snap = s
                break

        if target_lam is None:
            lines.append("\n  {} vs {} ({}): drop={} 实际={}球 - 无法通过λ调整覆盖".format(
                m["home"], m["away"], m["lg"], drop, actual))
            continue

        # 反算需要的 drop_adj
        # λ = GL * calib * (1 + s + f + drop_adj) * low_score_factor
        base = 1.0 + m["s"] + m["f"]
        ls = m["ls_factor"] if m["low_score"] and m["ls_factor"] else 1.0
        gl = m["GL"]
        cal = m["calib"]
        needed_drop_adj = target_lam / (gl * cal * ls) - base

        # 诱盘部分: drop_adj = coef * (drop - 1.0)
        portion = drop - 1.0
        needed_coef = needed_drop_adj / portion if portion > 0 else float("inf")

        lines.append("")
        lines.append("  {} vs {} ({})  drop={}".format(m["home"], m["away"], m["lg"], drop))
        lines.append("    实际={}球  当前lam={:.2f} SNAP 未覆盖".format(actual, m["cur_lam"]))
        lines.append("    需要 lam>={:.2f}  SNAP={}".format(target_lam, target_snap))
        lines.append("    GL={} calib={:.3f}  s={:+.4f} f={:+.4f}  base=1+s+f={:.4f}".format(
            gl, cal, m["s"], m["f"], base))
        lines.append("    low_score={} factor={}".format(m["low_score"], ls))
        lines.append("    公式: {:.2f} = {:.1f} * {:.3f} * ({:.4f} + d) * {}".format(
            target_lam, gl, cal, base, ls))
        lines.append("    d = {:.4f}".format(needed_drop_adj))
        lines.append("    drop_adj = coef * (drop - 1.0) = coef * {:.2f}".format(portion))
        lines.append("    >>> 需要系数 >= {:.4f}".format(needed_coef))
        lines.append("    >>> 相对 drop_sensitivity=0.05 的倍数: {:.1f}x".format(needed_coef/0.05))

        # 也计算不需要 low_score 的情况作为对比
        if m["low_score"]:
            needed_d2 = target_lam / (gl * cal * 1.0) - base
            needed_coef2 = needed_d2 / portion if portion > 0 else float("inf")
            lines.append("    >>> 如果去掉low_score规则: 需要系数 >= {:.4f} ({:.1f}x)".format(
                needed_coef2, needed_coef2/0.05))

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Done ->", OUTPUT)

asyncio.run(main())
