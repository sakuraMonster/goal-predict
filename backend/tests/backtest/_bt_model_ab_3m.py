"""近3个月模型A/B 回测：用训练好的模型对已结算比赛重新预测，统计命中率/MAE"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np, pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match
from app.predictor.features_a import FeatureEngineerA
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB


async def main():
    async with async_session() as db:
        model_a = ModelA()
        model_b = ModelB()
        print(f"模型A加载: wl={'有' if model_a.model_wl else '无'} hcp={'有' if model_a.model_hcp else '无'}")
        print(f"模型B加载: {'有' if model_b.model else '无'} 特征数={len(model_b._feature_names) if model_b._feature_names else 0}")

        # 近3个月（北京时间 naive）
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
        print(f"\n近3个月已结算比赛: {len(matches)} 场 ({start.date()} ~ {cutoff.date()})")

        feat_a = FeatureEngineerA(db)
        feat_b = FeatureEngineerB(db)

        wl_hit = wl_total = 0
        hcp_hit = hcp_total = 0
        goals_mae_sum = goals_total = 0
        over25_hit = over25_total = 0
        failed = 0

        for i, m in enumerate(matches):
            if (i + 1) % 200 == 0:
                print(f"  {i+1}/{len(matches)} (失败{failed})")
            try:
                # 模型A：胜平负 + 让球
                fa = await feat_a.extract_features(m.id)
                if fa.empty:
                    raise ValueError("模型A特征空")
                pa = model_a.predict(fa)
                actual_wl = 0 if m.home_score > m.away_score else (1 if m.home_score == m.away_score else 2)
                pred_wl = int(np.argmax([pa["home_prob"], pa["draw_prob"], pa["away_prob"]]))
                if pred_wl == actual_wl:
                    wl_hit += 1
                wl_total += 1
                if m.handicap_line and m.handicap_line != 0:
                    adj = m.home_score + m.handicap_line
                    actual_hcp = 0 if adj > m.away_score else (1 if adj == m.away_score else 2)
                    pred_hcp = int(np.argmax([pa["handicap_home_prob"], pa["handicap_draw_prob"], pa["handicap_away_prob"]]))
                    if pred_hcp == actual_hcp:
                        hcp_hit += 1
                    hcp_total += 1

                # 模型B：进球 + 大2.5
                fb = await feat_b.extract_features(m.id, sm_prediction=None)
                if fb.empty:
                    raise ValueError("模型B特征空")
                pb = model_b.predict(fb)
                actual_goals = m.home_score + m.away_score
                goals_mae_sum += abs(pb["expected_goals"] - actual_goals)
                goals_total += 1
                actual_over = 1 if actual_goals > 2.5 else 0
                pred_over = 1 if pb["over_2_5_prob"] > 0.5 else 0
                if pred_over == actual_over:
                    over25_hit += 1
                over25_total += 1
            except Exception as e:
                failed += 1
                if failed <= 5:
                    print(f"  [错误] match_id={m.id}: {e}")

        print(f"\n=== 回测结果（近3个月）===")
        print(f"有效样本: {wl_total} 场, 失败: {failed}")
        if wl_total:
            print(f"胜平负准确率: {wl_hit/wl_total*100:.1f}% ({wl_hit}/{wl_total})")
        if hcp_total:
            print(f"让球准确率:   {hcp_hit/hcp_total*100:.1f}% ({hcp_hit}/{hcp_total})")
        if goals_total:
            print(f"进球MAE:      {goals_mae_sum/goals_total:.3f} (样本{goals_total})")
        if over25_total:
            print(f"大2.5命中率:  {over25_hit/over25_total*100:.1f}% ({over25_hit}/{over25_total})")


asyncio.run(main())
