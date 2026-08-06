"""对指定比赛日的比赛执行预测，使用竞彩比赛日周期 [当日12:00, 次日12:00)"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select


async def main(target_date: str = "2026-08-03"):
    """target_date: 比赛日 YYYY-MM-DD，周期为 [当日12:00, 次日12:00)"""
    d = datetime.strptime(target_date, "%Y-%m-%d")
    start = d.replace(hour=12, minute=0, second=0)
    end = start + timedelta(hours=24)

    async with async_session() as db:
        result = await db.execute(
            select(Match).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.kickoff_time >= start,
                Match.kickoff_time < end,
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        print(f"待预测比赛: {len(matches)} 场")

        pipeline = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")
        created, updated, failed = 0, 0, 0

        for i, m in enumerate(matches):
            home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"

            try:
                result_data = await pipeline.predict(m.id)
            except Exception as e:
                failed += 1
                print(f"  FAIL ID={m.id} {home} vs {away}: {e}")
                continue

            existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = existing.scalar_one_or_none()

            if pred:
                pred.home_prob = result_data["home_prob"]
                pred.draw_prob = result_data["draw_prob"]
                pred.away_prob = result_data["away_prob"]
                pred.handicap_home_prob = result_data["handicap_home_prob"]
                pred.handicap_draw_prob = result_data["handicap_draw_prob"]
                pred.handicap_away_prob = result_data["handicap_away_prob"]
                pred.expected_goals = result_data["expected_goals"]
                pred.over_2_5_prob = result_data["over_2_5_prob"]
                pred.goal_distribution = result_data["goal_distribution"]
                pred.score_top5_json = result_data["score_top5_json"]
                pred.confidence_level = result_data["confidence_level"]
                pred.is_cold_match = result_data["is_cold_match"]
                pred.summary_text = result_data["summary_text"]
                pred.key_factors = result_data.get("key_factors", "")
                pred.risk_warning = json.dumps(result_data.get("data_quality", []), ensure_ascii=False)
                pred.model_version = version
                pred.kickoff_time = m.kickoff_time
                pred.league_id = m.league_id
                updated += 1
            else:
                pred = Prediction(
                    match_id=m.id,
                    model_version=version,
                    home_prob=result_data["home_prob"],
                    draw_prob=result_data["draw_prob"],
                    away_prob=result_data["away_prob"],
                    handicap_home_prob=result_data["handicap_home_prob"],
                    handicap_draw_prob=result_data["handicap_draw_prob"],
                    handicap_away_prob=result_data["handicap_away_prob"],
                    expected_goals=result_data["expected_goals"],
                    over_2_5_prob=result_data["over_2_5_prob"],
                    goal_distribution=result_data["goal_distribution"],
                    score_top5_json=result_data["score_top5_json"],
                    confidence_level=result_data["confidence_level"],
                    is_cold_match=result_data["is_cold_match"],
                    summary_text=result_data["summary_text"],
                    key_factors=result_data.get("key_factors", ""),
                    risk_warning=json.dumps(result_data.get("data_quality", []), ensure_ascii=False),
                    kickoff_time=m.kickoff_time,
                    league_id=m.league_id,
                )
                db.add(pred)
                created += 1

            print(f"  OK  ID={m.id} {home} vs {away} | {'新增' if not existing else '更新'}")

            if (i + 1) % 10 == 0:
                await db.commit()

        await db.commit()
        print(f"\n完成: 新增 {created}, 更新 {updated}, 失败 {failed}")


if __name__ == "__main__":
    asyncio.run(main())
