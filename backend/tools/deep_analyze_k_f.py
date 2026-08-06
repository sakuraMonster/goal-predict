"""韩K 和 芬超 Model C 详细分析"""
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import joinedload
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import Match, Prediction, League
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

TARGET_LEAGUES = [6, 10]  # 韩K=6, 芬超=10

async def main():
    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        model_c = ModelC()

        result = await db.execute(select(League.id, League.name_zh))
        league_names = {row[0]: row[1] for row in result}

        dates = ["2026-07-25", "2026-07-26", "2026-07-27"]
        matches_data = []

        for day_str in dates:
            d_start = datetime.strptime(day_str, "%Y-%m-%d")
            qs = d_start.replace(hour=12)
            qe = (d_start + timedelta(days=1)).replace(hour=12)
            result = await db.execute(
                select(Match).options(joinedload(Match.league))
                .where(Match.kickoff_time >= qs, Match.kickoff_time < qe,
                       Match.league_id.in_(TARGET_LEAGUES))
                .order_by(Match.kickoff_time)
            )
            for m in result.unique().scalars().all():
                lg_name = league_names.get(m.league_id, "?")
                pred_r = await db.execute(
                    select(Prediction).where(Prediction.match_id == m.id))
                pred = pred_r.scalar_one_or_none()

                features_df = await feat_engine.extract_features(m.id)
                if features_df.empty:
                    continue
                features = features_df.iloc[0].to_dict()
                rc = model_c.predict(features, lg_name)
                d = rc["detail"]
                snap = snap_top2(rc["expected_goals"])
                total = pred.actual_total_goals if pred else None
                hit = total in snap if total is not None else None

                matches_data.append({
                    "lg": lg_name, "date": day_str,
                    "home": m.home_team_name, "away": m.away_team_name,
                    "score": pred.actual_score if pred else "?",
                    "total": total,
                    "lam": rc["expected_goals"], "snap": snap, "hit": hit,
                    "GL": d["goal_line"], "calib": d["calib"],
                    "s": d["strength_adj"], "f": d["form_adj"], "dp": d["drop_adj"],
                    "raw": d["lambda_raw"],
                    "low": d.get("low_score_applied", False),
                    "home_gf": d["home_goals_avg"], "away_gf": d["away_goals_avg"],
                    "home_f6": d["home_gf_avg_6"], "away_f6": d["away_gf_avg_6"],
                    "drop": d["goal_drop"],
                })

    # ====== 输出 ======
    for lg in ["韩K", "芬超"]:
        league_data = [m for m in matches_data if m["lg"] == lg]
        hit_c = sum(1 for m in league_data if m["hit"])
        total_c = len(league_data)
        acc = hit_c / total_c * 100 if total_c else 0
        print("=" * 75)
        print(f"  {lg}: {hit_c}/{total_c} = {acc:.1f}%")
        print("=" * 75)

        league_data.sort(key=lambda x: abs(x["total"] - x["lam"]) if x["total"] else 0, reverse=True)

        for m in league_data:
            delta = m["total"] - m["lam"] if m["total"] is not None else 0
            status = "HIT" if m["hit"] else "MISS"
            if m["total"] and m["total"] > m["lam"] + 0.5:
                arrow = ">>> 实际远高于预测"
            elif m["total"] and m["total"] < m["lam"] - 0.5:
                arrow = "<<< 实际远低于预测"
            else:
                arrow = " ~  偏差在0.5内"
            low_tag = " [低分规则触发!]" if m["low"] else ""

            total_atk = m["home_gf"] + m["away_gf"]
            print("")
            print("  {} {} {} vs {} ({})".format(status, arrow, m["home"], m["away"], m["date"]))
            print("    比分: {}  总进球={}  lam={:.2f}  SNAP={}  偏差={:+.1f}{}".format(
                m["score"], m["total"], m["lam"], m["snap"], delta, low_tag))
            print("    盘口: GL={}  calib={:.3f}  drop={}".format(m["GL"], m["calib"], m["drop"]))
            print("    攻击力: 主{:.2f} + 客{:.2f} = {:.2f}  |  strength_adj={:+.4f}".format(
                m["home_gf"], m["away_gf"], total_atk, m["s"]))
            print("    近期:   主{:.2f} + 客{:.2f}          |  form_adj={:+.4f}".format(
                m["home_f6"], m["away_f6"], m["f"]))
            print("    drop_adj={:+.4f}  raw_lambda={:.4f}".format(m["dp"], m["raw"]))

            # 诊断
            issues = []
            if m["GL"] >= 3.0 and m["total"] and m["total"] <= 2:
                issues.append("大盘口({})但实际小球({}球)，市场盘口高估".format(m["GL"], m["total"]))
            if m["GL"] <= 2.25 and m["total"] and m["total"] >= 4:
                issues.append("小盘口({})但实际大球({}球)，市场盘口低估".format(m["GL"], m["total"]))
            if m["dp"] <= -0.05:
                issues.append("盘口回落drop={}拖累lam {:.3f}".format(m["drop"], m["dp"]))
            if m["s"] > 0.05 and m["total"] and m["total"] <= 2:
                issues.append("攻击力高估(strength={:+.3f})，实际小球".format(m["s"]))
            if m["s"] < -0.02 and m["total"] and m["total"] >= 4:
                issues.append("攻击力低估(strength={:+.3f})，实际大球".format(m["s"]))
            if m["low"]:
                issues.append("低分规则额外x0.75，总进球{}但预测被大幅压低".format(m["total"]))
            if m["total"] and m["total"] not in m["snap"] and abs(delta) <= 1.5:
                issues.append("SNAP Top2({})未覆盖实际{}球（偏差仅{:.1f}，属SNAP机制损耗）".format(
                    m["snap"], m["total"], delta))
            if m["calib"] > 1.05 and m["total"] and m["total"] <= 2:
                issues.append("calib={:.3f}偏高，联赛校正常数高估了进球".format(m["calib"]))

            for issue in issues:
                print("    >> {}".format(issue))

        # 汇总分析
        print("")
        print("  --- {} 失败根因汇总 ---".format(lg))
        misses = [m for m in league_data if m["hit"] is False]
        oversized = sum(1 for m in misses if m["total"] and m["total"] > m["lam"] + 0.5)
        undersized = sum(1 for m in misses if m["total"] and m["total"] < m["lam"] - 0.5)
        snap_loss = sum(1 for m in misses if m["total"] and abs(m["total"] - m["lam"]) <= 1.5)
        drop_issue = sum(1 for m in misses if m["dp"] <= -0.05)
        gl_high = sum(1 for m in misses if m["GL"] >= 3.0)
        print("  高估(预测>实际>0.5): {}场, 低估(实际>预测>0.5): {}场".format(undersized, oversized))
        print("  SNAP机制损耗(偏差<=1.5但未覆盖): {}场".format(snap_loss))
        print("  盘口回落拖累(drop_adj<=-0.05): {}场".format(drop_issue))
        print("  大盘口小球(GL>=3.0): {}场".format(gl_high))
        print("")

asyncio.run(main())
