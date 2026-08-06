"""预测未来比赛"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from datetime import datetime
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.pipeline import PredictionPipeline

async def main():
    async with async_session() as db:
        # 获取所有未来比赛（包括今天的）
        mr = await db.execute(
            select(Match).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.id >= 15469,  # 周五001开始
            ).order_by(Match.id)
        )
        matches = list(mr.scalars().all())
        print(f"待预测: {len(matches)} 场")

        pipeline = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")
        created, updated, failed = 0, 0, 0

        for i, m in enumerate(matches):
            try:
                pred_result = await pipeline.predict(m.id)
                
                # 检查是否已有预测
                existing = await db.execute(
                    select(Prediction).where(Prediction.match_id == m.id)
                )
                old = existing.scalar_one_or_none()

                hp = pred_result.get("home_prob", 0)
                dp = pred_result.get("draw_prob", 0)
                ap = pred_result.get("away_prob", 0)
                max_p = max(hp, dp, ap)
                pred_spf = "主胜" if hp == max_p else ("平局" if dp == max_p else "客胜")
                cold = "Y" if pred_result.get("is_cold_match") else "-"
                goals = pred_result.get("expected_goals", 0)
                
                print(f"  [{i+1}/{len(matches)}] {m.match_num} {m.home_team_name} vs {m.away_team_name}")
                print(f"    预测: {pred_spf} ({hp:.3f}/{dp:.3f}/{ap:.3f}) 进球{goals:.1f} 冷门={cold}")

                if old:
                    old.home_prob = hp
                    old.draw_prob = dp
                    old.away_prob = ap
                    old.expected_goals = goals
                    old.model_version = version
                    old.is_cold_match = pred_result.get("is_cold_match", False)
                    updated += 1
                else:
                    new_pred = Prediction(
                        match_id=m.id,
                        home_prob=hp, draw_prob=dp, away_prob=ap,
                        expected_goals=goals,
                        model_version=version,
                        is_cold_match=pred_result.get("is_cold_match", False),
                    )
                    db.add(new_pred)
                    created += 1

            except Exception as e:
                failed += 1
                print(f"    FAILED: {e.__class__.__name__}: {e}")

        await db.commit()
        print(f"\n完成! 新增 {created}, 更新 {updated}, 失败 {failed}")

asyncio.run(main())
