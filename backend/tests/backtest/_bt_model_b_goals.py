"""近3个月模型B 进球数细粒度回测：精确/top2命中、各档分布、大小球校准、混淆矩阵"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np, pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_b import ModelB
from app.predictor.models.implied import rps, brier_multiclass


async def main():
    async with async_session() as db:
        model_b = ModelB()
        print(f"模型B: {'有' if model_b.model else '无'} 特征数={len(model_b._feature_names) if model_b._feature_names else 0} scaler={'有' if model_b.scaler else '无'}", flush=True)

        cutoff = datetime(2026, 10, 21)
        start = cutoff - timedelta(days=90)
        result = await db.execute(
            select(Match).where(
                Match.home_score.isnot(None),
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.status == "finished",
                Match.kickoff_time >= start,
                Match.kickoff_time < cutoff,
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        print(f"近3个月已结算比赛: {len(matches)} 场 ({start.date()} ~ {cutoff.date()})", flush=True)

        feat_b = FeatureEngineerB(db)

        exact_hit = top2_hit = total = 0
        over25_hit = over25_total = 0
        mae_sum = brier_sum = logloss_sum = 0.0
        rps_sum = mc_brier_sum = 0.0
        actual_dist = np.zeros(5)
        pred_prob_sum = np.zeros(5)
        bucket_hit = np.zeros(5)
        bucket_total = np.zeros(5)
        confusion = np.zeros((5, 5), dtype=int)

        for i, m in enumerate(matches):
            if (i + 1) % 200 == 0:
                print(f"  {i+1}/{len(matches)}", flush=True)
            try:
                fb = await feat_b.extract_features(m.id, sm_prediction=None)
                if fb.empty:
                    continue
                pb = model_b.predict(fb)
                actual = m.home_score + m.away_score
                dist = pb["goal_distribution"]  # [P0,P1,P2,P3,P4+]

                pred = int(np.argmax(dist))
                actual_bucket = min(actual, 4)
                if pred == actual_bucket:
                    exact_hit += 1
                top2 = set(np.argsort(dist)[-2:])
                if actual_bucket in top2:
                    top2_hit += 1
                total += 1

                actual_dist[actual_bucket] += 1
                pred_prob_sum += np.array(dist)
                bucket_total[actual_bucket] += 1
                if pred == actual_bucket:
                    bucket_hit[actual_bucket] += 1
                confusion[actual_bucket, pred] += 1

                over25 = pb["over_2_5_prob"]
                actual_over = 1 if actual > 2.5 else 0
                pred_over = 1 if over25 > 0.5 else 0
                if pred_over == actual_over:
                    over25_hit += 1
                over25_total += 1
                brier_sum += (over25 - actual_over) ** 2
                logloss_sum += -np.log(max(dist[actual_bucket], 1e-12))
                mae_sum += abs(pb["expected_goals"] - actual)
                rps_sum += rps(dist, actual_bucket)
                mc_brier_sum += brier_multiclass(dist, actual_bucket)
            except Exception as e:
                print(f"  [err] match_id={m.id}: {e}", flush=True)

        print(f"\n=== 模型B 进球数细粒度回测（近3个月，{total} 场）===", flush=True)
        print(f"进球数精确命中率(top1): {exact_hit/total*100:.1f}% ({exact_hit}/{total})", flush=True)
        print(f"进球数 top-2 命中率:     {top2_hit/total*100:.1f}% ({top2_hit}/{total})", flush=True)
        print(f"进球MAE: {mae_sum/total:.3f}", flush=True)
        print(f"多分类 log loss: {logloss_sum/total:.4f}", flush=True)
        print(f"RPS(5档): {rps_sum/total:.5f}", flush=True)
        print(f"多分类 Brier(5档): {mc_brier_sum/total:.5f}", flush=True)

        print(f"\n各档命中（预测档 = 实际档）:", flush=True)
        labels = ["0球", "1球", "2球", "3球", "4+球"]
        for k in range(5):
            if bucket_total[k] > 0:
                print(f"  {labels[k]}: 实际{bucket_total[k]:.0f}场({bucket_total[k]/total*100:.1f}%) 命中{bucket_hit[k]:.0f}场 档内命中率{bucket_hit[k]/bucket_total[k]*100:.1f}% 预测概率均值{pred_prob_sum[k]/total*100:.1f}%", flush=True)

        print(f"\n混淆矩阵（行=实际, 列=预测top1）:", flush=True)
        print("        " + "  ".join(f"{labels[k]:>5}" for k in range(5)), flush=True)
        for r in range(5):
            print(f"  {labels[r]:>4} " + "  ".join(f"{confusion[r,c]:>5}" for c in range(5)), flush=True)

        print(f"\n大2.5命中率: {over25_hit/over25_total*100:.1f}% ({over25_hit}/{over25_total})", flush=True)
        print(f"大2.5 Brier分数: {brier_sum/over25_total:.4f} (0=完美, 0.25=随机)", flush=True)
        print(f"\n实际进球分布: " + "  ".join(f"{labels[k]}={actual_dist[k]/total*100:.1f}%" for k in range(5)), flush=True)
        print(f"预测概率均值: " + "  ".join(f"{labels[k]}={pred_prob_sum[k]/total*100:.1f}%" for k in range(5)), flush=True)


asyncio.run(main())
