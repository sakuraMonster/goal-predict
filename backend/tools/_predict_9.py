"""对9场未来比赛重新预测"""
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
        now = datetime.utcnow()
        result = await db.execute(
            select(Match).where(
                Match.kickoff_time >= now,
                Match.match_num.isnot(None),
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
            ).order_by(Match.kickoff_time)
        )
        matches = result.scalars().all()
        print(f"未来比赛: {len(matches)} 场")

        pipeline = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")

        for m in matches:
            try:
                r = await pipeline.predict(m.id)
            except Exception as e:
                print(f"  {m.match_num} (id={m.id}) 预测失败: {e}")
                continue

            existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = existing.scalar_one_or_none()

            if pred:
                pred.home_prob = r["home_prob"]
                pred.draw_prob = r["draw_prob"]
                pred.away_prob = r["away_prob"]
                pred.handicap_home_prob = r["handicap_home_prob"]
                pred.handicap_draw_prob = r["handicap_draw_prob"]
                pred.handicap_away_prob = r["handicap_away_prob"]
                pred.expected_goals = r["expected_goals"]
                pred.over_2_5_prob = r["over_2_5_prob"]
                pred.goal_distribution = r["goal_distribution"]
                pred.score_top5_json = r["score_top5_json"]
                pred.confidence_level = r["confidence_level"]
                pred.is_cold_match = r["is_cold_match"]
                pred.cold_correction = r.get("cold_correction")
                pred.summary_text = r["summary_text"]
                pred.key_factors = r.get("key_factors", "")
                pred.model_version = version
                pred.kickoff_time = m.kickoff_time
                pred.league_id = m.league_id
            else:
                pred = Prediction(
                    match_id=m.id, model_version=version,
                    home_prob=r["home_prob"], draw_prob=r["draw_prob"], away_prob=r["away_prob"],
                    handicap_home_prob=r["handicap_home_prob"], handicap_draw_prob=r["handicap_draw_prob"], handicap_away_prob=r["handicap_away_prob"],
                    expected_goals=r["expected_goals"], over_2_5_prob=r["over_2_5_prob"],
                    goal_distribution=r["goal_distribution"], score_top5_json=r["score_top5_json"],
                    confidence_level=r["confidence_level"], is_cold_match=r["is_cold_match"],
                    cold_correction=r.get("cold_correction"),
                    summary_text=r["summary_text"], key_factors=r.get("key_factors", ""),
                    kickoff_time=m.kickoff_time, league_id=m.league_id,
                )
                db.add(pred)

            ht = r.get("home_team_name", "")
            at = r.get("away_team_name", "")
            spf = f"{r['home_prob']:.1%}/{r['draw_prob']:.1%}/{r['away_prob']:.1%}"
            cold = " [冷]" if r.get("is_cold_match") else ""
            print(f"  {m.match_num} {ht} vs {at}: SPF={spf} goals={r['expected_goals']:.1f}{cold}")

        await db.commit()
        print(f"\n完成，版本: {version}")

if __name__ == "__main__":
    asyncio.run(main())
