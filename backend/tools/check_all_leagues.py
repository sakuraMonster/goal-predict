"""
全量联赛重预测验证脚本：2026-07-28 ~ 2026-08-03
- 从 Prediction 表读取已有实际比分的所有场次
- 用 PredictionPipeline 进行预测（自动应用所有当前配置）
- 统计 SNAP Top2 命中率，按联赛分组汇总
- 与之前 53.1% 基准做对比
"""
import asyncio
import sys
import os
import math
import json
from datetime import datetime
from collections import defaultdict, OrderedDict

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import joinedload
from app.db.database import engine
from app.db.models import Prediction, Match, League
from app.predictor.pipeline import PredictionPipeline


def calc_snap(adj_lam: float) -> tuple[list[int], str]:
    """根据调整后 λ 计算 SNAP Top2 和最接近的整数"""
    if adj_lam <= 0:
        return [0, 1], "0/1"
    eg_clean = round(adj_lam, 10)
    frac = eg_clean - math.floor(eg_clean)
    if frac < 0.10:
        eff = math.floor(eg_clean)
    elif frac > 0.90:
        eff = math.ceil(eg_clean)
    else:
        eff = adj_lam
    dists = sorted([(abs(eff - k), k) for k in range(5)])
    top2 = sorted([dists[0][1], dists[1][1]])
    return top2, f"{top2[0]}/{top2[1]}"


BASELINE = 53.1  # 之前基准命中率


async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 7, 28, 12, 0, 0)
    end = datetime(2026, 8, 4, 12, 0, 0)

    async with sf() as db:
        # ── 查询有实际比分的预测 ──
        result = await db.execute(
            select(Prediction)
            .options(
                joinedload(Prediction.match).joinedload(Match.home_team),
                joinedload(Prediction.match).joinedload(Match.away_team),
                joinedload(Prediction.match).joinedload(Match.league),
                joinedload(Prediction.league),
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
        print()

        if not preds:
            print("[WARN] 无数据")
            return

        pipeline = PredictionPipeline(db)
        results = []

        # ══════════════════════════════════════════════════════════════
        #  a. 逐场明细
        # ══════════════════════════════════════════════════════════════
        print("=" * 140)
        print("  全量联赛重预测验证：2026-07-28 ~ 2026-08-03")
        print("  配置：LEAGUE_LAMBDA_CALIBRATION + MARKET_ATTENUATION + MARKET_ADJUSTMENT_WEIGHT")
        print("=" * 140)
        print()
        print(f"  {'#':<4} {'联赛ID':<7} {'联赛':<12} {'主队':<14} {'客队':<14} "
              f"{'raw_λ':<7} {'调整λ':<7} {'SNAP Top2':<9} {'实际T':<6} {'命中':<4} {'日期':<11}")
        print("  " + "-" * 136)

        for i, p in enumerate(preds):
            mid = p.match_id
            m = p.match
            if not m:
                print(f"  [{i+1}] MID={mid} NO MATCH, skip")
                continue

            home_name = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away_name = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
            lg = p.league.name_zh if p.league else (m.league.name_zh if m.league else "?")
            lg_id = p.league_id or (m.league_id or 0)
            kt = p.kickoff_time.strftime("%m-%d %H:%M") if p.kickoff_time else "?"
            actual_h = p.actual_home_score or 0
            actual_a = p.actual_away_score or 0
            actual_total = actual_h + actual_a

            # ── 使用 Pipeline 重预测 ──
            try:
                new_pred = await pipeline.predict(mid)
            except Exception as e:
                print(f"  [{i+1:3d}] MID={mid} PREDICT FAIL: {type(e).__name__}: {e}")
                continue

            raw_lam = new_pred.get("raw_lambda", 0)
            adj_lam = new_pred.get("expected_goals", 0)

            # ── SNAP Top2 判定 ──
            top2, top2_str = calc_snap(adj_lam)
            act = min(actual_total, 4)
            snap_hit = act in top2

            r = {
                "id": mid,
                "league_id": lg_id,
                "league": lg,
                "home": home_name,
                "away": away_name,
                "kickoff": kt,
                "actual_h": actual_h,
                "actual_a": actual_a,
                "actual_total": actual_total,
                "raw_lambda": raw_lam,
                "expected_goals": adj_lam,
                "snap_top2": top2_str,
                "snap_hit": snap_hit,
            }
            results.append(r)

            H = "✅" if snap_hit else "❌"
            print(f"  {i+1:<4} {lg_id:<7} {lg:<12} {home_name:<14} {away_name:<14} "
                  f"{raw_lam:<7.2f} {adj_lam:<7.2f} {top2_str:<9} "
                  f"{actual_total:<6} {H:<4} {kt:<11}")

        # ══════════════════════════════════════════════════════════════
        #  b. 按联赛分组汇总
        # ══════════════════════════════════════════════════════════════
        total = len(results)
        if total == 0:
            print("\n[WARN] 无预测结果")
            return

        ls = defaultdict(lambda: {"n": 0, "hit": 0, "raw_sum": 0.0, "adj_sum": 0.0, "matches": []})
        for r in results:
            lg = r["league"]
            ls[lg]["n"] += 1
            ls[lg]["hit"] += 1 if r["snap_hit"] else 0
            ls[lg]["raw_sum"] += r["raw_lambda"]
            ls[lg]["adj_sum"] += r["expected_goals"]
            ls[lg]["matches"].append(r)

        # 按场次数降序排列
        ls_sorted = sorted(ls.items(), key=lambda x: x[1]["n"], reverse=True)

        print()
        print("=" * 100)
        print("  按联赛分组汇总")
        print("=" * 100)
        print(f"  {'联赛':<14} {'联赛ID':<7} {'场次':<5} {'命中':<7} {'命中率':<9} {'均raw_λ':<8} {'均调整λ':<8}")
        print("  " + "-" * 98)

        for lg_name, s in ls_sorted:
            n = s["n"]
            h = s["hit"]
            pct = h / n * 100 if n > 0 else 0
            avg_raw = s["raw_sum"] / n if n > 0 else 0
            avg_adj = s["adj_sum"] / n if n > 0 else 0
            # 找 league_id（取第一个 match 的）
            lg_id = s["matches"][0]["league_id"] if s["matches"] else 0
            print(f"  {lg_name:<14} {lg_id:<7} {n:<5} {h}/{n:<5} {pct:<8.1f}% {avg_raw:<8.2f} {avg_adj:<8.2f}")

        # ══════════════════════════════════════════════════════════════
        #  c. 全量汇总
        # ══════════════════════════════════════════════════════════════
        total_hits = sum(1 for r in results if r["snap_hit"])
        hit_rate = total_hits / total * 100
        avg_raw_all = sum(r["raw_lambda"] for r in results) / total
        avg_adj_all = sum(r["expected_goals"] for r in results) / total
        avg_actual_all = sum(r["actual_total"] for r in results) / total

        print()
        print("=" * 100)
        print("  全量汇总")
        print("=" * 100)
        print(f"  总场次:           {total}")
        print(f"  总命中:           {total_hits}")
        print(f"  总命中率:         {hit_rate:.1f}%")
        print(f"  平均 raw_λ:       {avg_raw_all:.2f}")
        print(f"  平均 调整后λ:     {avg_adj_all:.2f}")
        print(f"  平均 实际总进球:  {avg_actual_all:.2f}")

        # ══════════════════════════════════════════════════════════════
        #  d. 与 53.1% 基准对比
        # ══════════════════════════════════════════════════════════════
        diff = hit_rate - BASELINE
        status = "↑ 提升" if diff > 0 else ("↓ 下降" if diff < 0 else "持平")
        print()
        print("=" * 100)
        print("  与 53.1% 基准对比")
        print("=" * 100)
        print(f"  基准命中率:       {BASELINE:.1f}%")
        print(f"  本次命中率:       {hit_rate:.1f}%")
        print(f"  差值:             {diff:+.1f}%  ({status})")

        # ── 未命中场次列表 ──
        missed = [r for r in results if not r["snap_hit"]]
        if missed:
            print()
            print("=" * 140)
            print(f"  未命中明细 ({len(missed)} 场)")
            print("=" * 140)
            print(f"  {'#':<4} {'联赛ID':<7} {'联赛':<12} {'主队':<14} {'客队':<14} "
                  f"{'raw_λ':<7} {'调整λ':<7} {'SNAP':<8} {'实际':<6} {'比分':<7} {'日期':<11}")
            print("  " + "-" * 136)
            for i, r in enumerate(missed):
                print(f"  {i+1:<4} {r['league_id']:<7} {r['league']:<12} {r['home']:<14} {r['away']:<14} "
                      f"{r['raw_lambda']:<7.2f} {r['expected_goals']:<7.2f} {r['snap_top2']:<8} "
                      f"{r['actual_total']:<6} {r['actual_h']}:{r['actual_a']:<5} {r['kickoff']:<11}")

        # ── 保存 JSON ──
        out_json = {
            "period": "2026-07-28 ~ 2026-08-03",
            "total": total,
            "hits": total_hits,
            "hit_rate": round(hit_rate, 1),
            "baseline": BASELINE,
            "diff_from_baseline": round(diff, 1),
            "avg_raw_lambda": round(avg_raw_all, 2),
            "avg_adj_lambda": round(avg_adj_all, 2),
            "avg_actual_total": round(avg_actual_all, 2),
            "by_league": OrderedDict(
                (lg_name, {
                    "league_id": s["matches"][0]["league_id"] if s["matches"] else 0,
                    "matches": s["n"],
                    "hits": s["hit"],
                    "hit_rate": round(s["hit"] / s["n"] * 100, 1) if s["n"] > 0 else 0,
                    "avg_raw_lambda": round(s["raw_sum"] / s["n"], 2) if s["n"] > 0 else 0,
                    "avg_adj_lambda": round(s["adj_sum"] / s["n"], 2) if s["n"] > 0 else 0,
                })
                for lg_name, s in ls_sorted
            ),
            "results": [
                {k: v for k, v in r.items()}
                for r in results
            ],
            "missed": [
                {k: v for k, v in r.items()}
                for r in missed
            ],
        }
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_all_leagues_result.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(out_json, f, ensure_ascii=False, default=str, indent=2)
        print(f"\n  完整结果已保存: {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
