"""
模型B 回测分析：07-28 ~ 08-03
- 从 predictions 表读取已有实际比分
- 用 Model B 独立重预测（仅进球数），对比命中情况
"""
import asyncio, sys, os, json, math
from datetime import datetime
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import joinedload
from app.db.database import engine
from app.db.models import Prediction, Match, League
from app.predictor.pipeline import PredictionPipeline


async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 7, 28, 12, 0, 0)
    end = datetime(2026, 8, 4, 12, 0, 0)

    async with sf() as db:
        # 查询有实际比分的预测
        result = await db.execute(
            select(Prediction)
            .options(
                joinedload(Prediction.match).joinedload(Match.home_team),
                joinedload(Prediction.match).joinedload(Match.away_team),
                joinedload(Prediction.match).joinedload(Match.league),
            )
            .where(
                and_(
                    Prediction.kickoff_time >= start,
                    Prediction.kickoff_time < end,
                    Prediction.actual_home_score.isnot(None),
                    Prediction.actual_away_score.isnot(None),
                )
            )
            .order_by(Prediction.kickoff_time)
        )
        preds = list(result.unique().scalars().all())
        print(f"[INFO] 查询到 {len(preds)} 条已有比分的预测记录")

        if not preds:
            print("[WARN] 无数据")
            return

        pipeline = PredictionPipeline(db)
        results = []

        # ── 逐场重预测 ──
        print()
        print("=" * 130)
        print("  模型B 重预测：07-28 ~ 08-03")
        print("=" * 130)

        for i, p in enumerate(preds):
            mid = p.match_id
            m = p.match
            if not m:
                print(f"  [{i+1}] MID={mid} NO MATCH, skip")
                continue

            home_name = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away_name = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
            lg = p.league.name_zh if p.league else "?"
            kt = p.kickoff_time.strftime("%m-%d %H:%M") if p.kickoff_time else "?"
            actual_h = p.actual_home_score or 0
            actual_a = p.actual_away_score or 0
            actual_total = actual_h + actual_a

            try:
                new_pred = await pipeline.predict(mid)
            except Exception as e:
                print(f"  [{i+1}] MID={mid} PREDICT FAIL: {type(e).__name__}: {e}")
                continue

            raw_lam = new_pred.get("raw_lambda", 0)
            adj_lam = new_pred.get("expected_goals", 0)
            over_prob = new_pred.get("over_2_5_prob", 0.5)
            zip_p = new_pred.get("zero_inflation_prob", 0)
            gd = new_pred.get("goal_distribution", [])
            cold = new_pred.get("is_cold_match", False)

            actual_over = actual_total > 2.5
            pred_over = over_prob > 0.5
            hit_goals = (pred_over == actual_over)

            # SNAP Top2 判定
            snap_hit = False
            snap_top2_str = ""
            if adj_lam > 0:
                eg_clean = round(adj_lam, 10)  # 去浮点噪声
                frac = eg_clean - math.floor(eg_clean)
                if frac < 0.10:
                    eff = math.floor(eg_clean)
                elif frac > 0.90:
                    eff = math.ceil(eg_clean)
                else:
                    eff = adj_lam
                dists = sorted([(abs(eff - k), k) for k in range(5)])
                top2 = sorted([dists[0][1], dists[1][1]])
                act = min(actual_total, 4)
                snap_hit = act in top2
                snap_top2_str = f"{top2[0]}/{top2[1]}"

            r = {
                "id": mid,
                "league": lg,
                "home": home_name,
                "away": away_name,
                "kickoff": kt,
                "actual_score": f"{actual_h}:{actual_a}",
                "actual_total": actual_total,
                "actual_over": actual_over,
                "raw_lambda": raw_lam,
                "expected_goals": adj_lam,
                "over_2_5_prob": over_prob,
                "zero_inflation": zip_p,
                "goal_dist_0": gd[0] if len(gd) > 0 else 0,
                "goal_dist_1": gd[1] if len(gd) > 1 else 0,
                "goal_dist_2": gd[2] if len(gd) > 2 else 0,
                "goal_dist_3": gd[3] if len(gd) > 3 else 0,
                "goal_dist_4p": gd[4] if len(gd) > 4 else 0,
                "pred_over": pred_over,
                "hit_goals": hit_goals,
                "snap_hit": snap_hit,
                "snap_top2": snap_top2_str,
                "cold": cold,
                # store old pred
                "old_lambda": p.expected_goals,
                "old_result": p.result_goals,
            }
            results.append(r)

            status = "V" if hit_goals else "X"
            snap_s = "V" if snap_hit else "X"
            print(f"  [{i+1:3d}] {status} SNAP={snap_s} MID={mid:5d} {kt} | "
                  f"{lg:10s} {home_name:12s} vs {away_name:12s} | "
                  f"{actual_h}:{actual_a} (T={actual_total}) | "
                  f"λ={raw_lam:.2f}→{adj_lam:.2f} over={over_prob*100:.0f}% "
                  f"z={zip_p:.2f} top2={snap_top2_str} {'冷' if cold else ''}")

        # ── 汇总 ──
        total = len(results)
        if total == 0:
            print("\n[WARN] 无预测结果")
            return

        hits = sum(1 for r in results if r["hit_goals"])
        snap_hits = sum(1 for r in results if r["snap_hit"])
        actual_overs = sum(1 for r in results if r["actual_over"])
        actual_unders = total - actual_overs
        cor_over = sum(1 for r in results if r["actual_over"] and r["hit_goals"])
        cor_under = sum(1 for r in results if not r["actual_over"] and r["hit_goals"])

        print()
        print("=" * 80)
        print(f"  汇总 (共 {total} 场)")
        print("=" * 80)
        print(f"  大2.5方向命中率:  {hits}/{total} = {hits/total*100:.1f}%")
        print(f"  SNAP Top2命中率:   {snap_hits}/{total} = {snap_hits/total*100:.1f}%")
        print(f"  实际大球: {actual_overs} 场 ({actual_overs/total*100:.0f}%)  小球: {actual_unders} 场")
        print(f"  大球正确: {cor_over}/{actual_overs} ({cor_over/max(actual_overs,1)*100:.0f}%)  "
              f"小球正确: {cor_under}/{actual_unders} ({cor_under/max(actual_unders,1)*100:.0f}%)")

        # ── 详细表格 ──
        print()
        print("=" * 160)
        print("  逐场明细")
        print("=" * 160)
        hdr = (f"  {'#':<4} {'H':<2} {'S':<2} {'日期':<11} {'联赛':<10} {'主队':<14} {'客队':<14} "
               f"{'比分':<6} {'T':<3} {'raw_λ':<7} {'λ_adj':<7} {'大%':<6} "
               f"{'P0':<5} {'P1':<5} {'P2':<5} {'P3':<5} {'P4+':<5} {'top2':<6} {'冷':<2}")
        print(hdr)
        print("  " + "-" * 156)

        for i, r in enumerate(results):
            H = "V" if r["hit_goals"] else "X"
            S = "V" if r["snap_hit"] else "X"
            c = "冷" if r["cold"] else ""
            print(f"  {i+1:<4} {H:<2} {S:<2} {r['kickoff']:<11} {r['league']:<10} "
                  f"{r['home']:<14} {r['away']:<14} "
                  f"{r['actual_score']:<6} {r['actual_total']:<3} "
                  f"{r['raw_lambda']:<7.2f} {r['expected_goals']:<7.2f} {r['over_2_5_prob']*100:<5.0f}% "
                  f"{r['goal_dist_0']*100:<4.0f}% {r['goal_dist_1']*100:<4.0f}% "
                  f"{r['goal_dist_2']*100:<4.0f}% {r['goal_dist_3']*100:<4.0f}% "
                  f"{r['goal_dist_4p']*100:<4.0f}% {r['snap_top2']:<6} {c:<2}")

        # ── 按联赛 ──
        print()
        print("=" * 90)
        print("  按联赛汇总")
        print("=" * 90)
        ls = defaultdict(lambda: {"n": 0, "hit": 0, "snap": 0, "over": 0, "lam_sum": 0})
        for r in results:
            lg = r["league"]
            ls[lg]["n"] += 1
            ls[lg]["hit"] += 1 if r["hit_goals"] else 0
            ls[lg]["snap"] += 1 if r["snap_hit"] else 0
            ls[lg]["over"] += 1 if r["actual_over"] else 0
            ls[lg]["lam_sum"] += r["expected_goals"]

        print(f"  {'联赛':<14} {'场':<4} {'大2.5命中':<10} {'SNAP命中':<10} {'大球率':<9} {'均λ':<7}")
        for lg, s in sorted(ls.items()):
            n = s["n"]
            print(f"  {lg:<14} {n:<4} {s['hit']}/{n} {s['hit']/n*100:<6.1f}% "
                  f"{s['snap']}/{n} {s['snap']/n*100:<6.1f}% "
                  f"{s['over']}/{n} {s['over']/n*100:<5.0f}%   {s['lam_sum']/n:<7.2f}")

        # ── 未命中 ──
        missed = [r for r in results if not r["hit_goals"]]
        fp = [r for r in missed if r["pred_over"]]   # 预测大实为小
        fn = [r for r in missed if not r["pred_over"]]  # 预测小实为大

        print()
        print("=" * 120)
        print(f"  未命中分析 ({len(missed)} 场: FP={len(fp)} 预测大球实为小球, FN={len(fn)} 预测小球实为大球)")
        print("=" * 120)

        if fp:
            print(f"\n  ── FP: 预测大球→实际小球 ({len(fp)}场) ──")
            for r in fp:
                print(f"    {r['kickoff']} {r['league']:<10} {r['home']} vs {r['away']}: "
                      f"{r['actual_score']}({r['actual_total']}球) "
                      f"λ={r['raw_lambda']:.2f}→{r['expected_goals']:.2f} over={r['over_2_5_prob']*100:.0f}% "
                      f"P0={r['goal_dist_0']*100:.0f}% P1={r['goal_dist_1']*100:.0f}% P2={r['goal_dist_2']*100:.0f}%")

        if fn:
            print(f"\n  ── FN: 预测小球→实际大球 ({len(fn)}场) ──")
            for r in fn:
                print(f"    {r['kickoff']} {r['league']:<10} {r['home']} vs {r['away']}: "
                      f"{r['actual_score']}({r['actual_total']}球) "
                      f"λ={r['raw_lambda']:.2f}→{r['expected_goals']:.2f} over={r['over_2_5_prob']*100:.0f}% "
                      f"P3={r['goal_dist_3']*100:.0f}% P4+={r['goal_dist_4p']*100:.0f}%")

        # ── λ偏差 ──
        errors = [r["actual_total"] - r["expected_goals"] for r in results]
        mae = sum(abs(e) for e in errors) / total
        avg_e = sum(errors) / total
        print(f"\n  λ偏差: 均值={avg_e:+.2f}, MAE={mae:.2f}")

        # ── λ区间分析 ──
        print(f"\n  λ区间分析:")
        bins = [(0, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 3.5), (3.5, 5), (5, 10)]
        for lo, hi in bins:
            br = [r for r in results if lo <= r["expected_goals"] < hi]
            if not br:
                continue
            bh = sum(1 for r in br if r["hit_goals"])
            bo = sum(1 for r in br if r["actual_over"])
            print(f"    λ∈[{lo:.1f},{hi:.1f}): {len(br)}场 命中={bh}/{len(br)} ({bh/len(br)*100:.0f}%) "
                  f"实际大球={bo}")

        # ── 保存JSON ──
        out = {
            "period": "2026-07-28 ~ 2026-08-03",
            "total": total,
            "hits_goals": hits,
            "hit_rate": round(hits / total * 100, 1),
            "snap_hits": snap_hits,
            "snap_rate": round(snap_hits / total * 100, 1),
            "mae": round(mae, 2),
            "avg_error": round(avg_e, 2),
            "fp_count": len(fp),
            "fn_count": len(fn),
            "results": [
                {k: v for k, v in r.items() if k != "old_lambda" and k != "old_result"}
                for r in results
            ],
            "missed": [
                {k: v for k, v in r.items() if k != "old_lambda" and k != "old_result"}
                for r in missed
            ],
        }
        path = "model_b_analysis_result.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, default=str, indent=2)
        print(f"\n  完整结果已保存: {path}")


if __name__ == "__main__":
    asyncio.run(main())
