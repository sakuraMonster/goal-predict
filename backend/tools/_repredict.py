"""回测脚本：用新模型重新预测 08-01/08-02 全部比赛，覆盖旧预测"""
import asyncio, sys
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')

from datetime import datetime
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.pipeline import PredictionPipeline

async def main():
    async with async_session() as db:
        # 查询 07-28 ~ 08-03 比赛周期
        start = datetime(2026, 7, 28, 0, 0, 0)
        end = datetime(2026, 8, 4, 0, 0, 0)
        result = await db.execute(
            select(Match).where(
                Match.kickoff_time >= start,
                Match.kickoff_time < end,
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        print(f"找到 {len(matches)} 场比赛")

        pipeline = PredictionPipeline(db)
        re_predicted = 0
        failed = 0
        errors_detail = []

        for m in matches:
            try:
                pred_result = await pipeline.predict(m.id)
            except Exception as e:
                failed += 1
                errors_detail.append((m.id, str(e)))
                print(f"  [FAIL] ID={m.id} {m.home_team_name} vs {m.away_team_name}: {e}")
                continue

            # 查找或创建 Prediction 记录
            existing = await db.execute(
                select(Prediction).where(Prediction.match_id == m.id)
            )
            pred = existing.scalar_one_or_none()

            if pred:
                # 更新预测字段（保留实际赛果）
                pred.home_prob = pred_result["home_prob"]
                pred.draw_prob = pred_result["draw_prob"]
                pred.away_prob = pred_result["away_prob"]
                pred.handicap_home_prob = pred_result["handicap_home_prob"]
                pred.handicap_draw_prob = pred_result["handicap_draw_prob"]
                pred.handicap_away_prob = pred_result["handicap_away_prob"]
                pred.expected_goals = pred_result["expected_goals"]
                pred.over_2_5_prob = pred_result["over_2_5_prob"]
                pred.goal_distribution = pred_result["goal_distribution"]
                pred.snap_top2 = pred_result["snap_top2"]
                pred.score_top5_json = pred_result.get("score_top5_json")
                pred.summary_text = pred_result.get("summary_text", "")
                pred.key_factors = pred_result.get("key_factors", "")
                pred.confidence_level = pred_result.get("confidence_level", "")
                pred.is_cold_match = pred_result.get("is_cold_match", False)
                pred.cold_correction = pred_result.get("cold_correction")
                pred.model_version = version
                # SNAP: 使用预测结果中的 snap_top2 直接判定
                actual_total = (pred.actual_home_score or 0) + (pred.actual_away_score or 0)
                if pred.snap_top2:
                    pred.result_goals = 1 if actual_total in pred.snap_top2 else -1
            else:
                # 不存在则创建
                actual_total = (m.home_score or 0) + (m.away_score or 0)
                snap_data = pred_result.get("snap_top2", [])
                rg = 1 if actual_total in snap_data else -1 if snap_data else 0
                pred = Prediction(
                    match_id=m.id,
                    league_id=m.league_id,
                    kickoff_time=m.kickoff_time,
                    home_prob=pred_result["home_prob"],
                    draw_prob=pred_result["draw_prob"],
                    away_prob=pred_result["away_prob"],
                    handicap_home_prob=pred_result["handicap_home_prob"],
                    handicap_draw_prob=pred_result["handicap_draw_prob"],
                    handicap_away_prob=pred_result["handicap_away_prob"],
                    expected_goals=pred_result["expected_goals"],
                    over_2_5_prob=pred_result["over_2_5_prob"],
                    goal_distribution=pred_result["goal_distribution"],
                    score_top5_json=pred_result.get("score_top5_json"),
                    summary_text=pred_result.get("summary_text", ""),
                    key_factors=pred_result.get("key_factors", ""),
                    confidence_level=pred_result.get("confidence_level", ""),
                    is_cold_match=pred_result.get("is_cold_match", False),
                    cold_correction=pred_result.get("cold_correction"),
                    model_version="v4.11-zip",
                    actual_home_score=m.home_score,
                    actual_away_score=m.away_score,
                    actual_total_goals=actual_total,
                    actual_score=f"{m.home_score}:{m.away_score}" if m.home_score is not None else None,
                    result_goals=rg,
                )
                db.add(pred)

            re_predicted += 1
            lam = pred_result.get("raw_lambda", pred_result["expected_goals"])
            zp = pred_result.get("zero_inflation_prob", 0)
            print(f"  [OK] ID={m.id} {m.home_team_name} vs {m.away_team_name}: λ={pred_result['expected_goals']:.2f} (raw={lam:.2f}, zip={zp:.2f})")

        await db.commit()
        print(f"\n完成: 重新预测 {re_predicted} 场, 失败 {failed} 场")
        if errors_detail:
            for mid, err in errors_detail:
                print(f"  失败 ID={mid}: {err}")

asyncio.run(main())
