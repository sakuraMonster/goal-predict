"""批量生成预测并写入 prediction 表"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime

from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select


async def main():
    async with async_session() as db:
        # 查所有有球队的比赛（不限完赛，全部生成预测）
        result = await db.execute(
            select(Match).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        print(f"待预测比赛: {len(matches)} 场")

        pipeline = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")
        created, updated, failed = 0, 0, 0

        for i, m in enumerate(matches):
            if (i + 1) % 100 == 0:
                print(f"  进度: {i+1}/{len(matches)}")

            try:
                result = await pipeline.predict(m.id)
            except Exception as e:
                failed += 1
                if failed <= 3:
                    print(f"  预测失败 match_id={m.id}: {e}")
                continue

            # upsert
            existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = existing.scalar_one_or_none()

            if pred:
                pred.home_prob = result["home_prob"]
                pred.draw_prob = result["draw_prob"]
                pred.away_prob = result["away_prob"]
                pred.handicap_home_prob = result["handicap_home_prob"]
                pred.handicap_draw_prob = result["handicap_draw_prob"]
                pred.handicap_away_prob = result["handicap_away_prob"]
                pred.expected_goals = result["expected_goals"]
                pred.over_2_5_prob = result["over_2_5_prob"]
                pred.goal_distribution = result["goal_distribution"]
                pred.score_top5_json = result["score_top5_json"]
                pred.confidence_level = result["confidence_level"]
                pred.is_cold_match = result["is_cold_match"]
                pred.summary_text = result["summary_text"]
                pred.key_factors = result.get("key_factors", "")
                pred.model_version = version
                pred.kickoff_time = m.kickoff_time
                pred.league_id = m.league_id
                updated += 1
            else:
                pred = Prediction(
                    match_id=m.id,
                    model_version=version,
                    home_prob=result["home_prob"],
                    draw_prob=result["draw_prob"],
                    away_prob=result["away_prob"],
                    handicap_home_prob=result["handicap_home_prob"],
                    handicap_draw_prob=result["handicap_draw_prob"],
                    handicap_away_prob=result["handicap_away_prob"],
                    expected_goals=result["expected_goals"],
                    over_2_5_prob=result["over_2_5_prob"],
                    goal_distribution=result["goal_distribution"],
                    score_top5_json=result["score_top5_json"],
                    confidence_level=result["confidence_level"],
                    is_cold_match=result["is_cold_match"],
                    summary_text=result["summary_text"],
                    key_factors=result.get("key_factors", ""),
                    kickoff_time=m.kickoff_time,
                    league_id=m.league_id,
                )
                db.add(pred)
                created += 1

            # 每 50 条提交一次
            if (i + 1) % 50 == 0:
                await db.commit()

        await db.commit()
        print(f"\n完成: 新增 {created}, 更新 {updated}, 失败 {failed}")
        print(f"版本: {version}")

        # 验证
        count = await db.execute(select(Prediction))
        print(f"prediction 表总记录: {len(count.scalars().all())}")


if __name__ == "__main__":
    asyncio.run(main())
