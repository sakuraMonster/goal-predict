"""对昨日比赛重新预测"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

from datetime import datetime, timedelta
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select

async def main():
    async with async_session() as db:
        # 7月30日 12:00 ~ 7月31日 12:00
        start = datetime(2026, 7, 30, 12, 0, 0)
        end = datetime(2026, 7, 31, 12, 0, 0)
        r = await db.execute(
            select(Match).where(
                Match.kickoff_time >= start,
                Match.kickoff_time < end,
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
            ).order_by(Match.kickoff_time)
        )
        matches = r.scalars().all()
        print(f"昨日比赛: {len(matches)} 场")

        pipeline = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")

        for m in matches:
            try:
                result = await pipeline.predict(m.id)
            except Exception as e:
                print(f"  {m.match_num} (id={m.id}) FAIL: {e}")
                continue

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
                pred.cold_correction = result.get("cold_correction")
                pred.summary_text = result["summary_text"]
                pred.key_factors = result.get("key_factors", "")
                pred.model_version = version
            else:
                db.add(Prediction(
                    match_id=m.id, model_version=version,
                    home_prob=result["home_prob"], draw_prob=result["draw_prob"], away_prob=result["away_prob"],
                    handicap_home_prob=result["handicap_home_prob"], handicap_draw_prob=result["handicap_draw_prob"], handicap_away_prob=result["handicap_away_prob"],
                    expected_goals=result["expected_goals"], over_2_5_prob=result["over_2_5_prob"],
                    goal_distribution=result["goal_distribution"], score_top5_json=result["score_top5_json"],
                    confidence_level=result["confidence_level"], is_cold_match=result["is_cold_match"],
                    cold_correction=result.get("cold_correction"),
                    summary_text=result["summary_text"], key_factors=result.get("key_factors", ""),
                    kickoff_time=m.kickoff_time, league_id=m.league_id,
                ))

            actual = f"{m.home_score}-{m.away_score}" if m.home_score is not None else "?"
            spf = f"{result['home_prob']:.1%}/{result['draw_prob']:.1%}/{result['away_prob']:.1%}"
            hcp = f"{result['handicap_home_prob']:.1%}/{result['handicap_draw_prob']:.1%}/{result['handicap_away_prob']:.1%}"
            print(f"  {m.match_num or '?':>8} {actual:>5} SPF={spf} HCP={hcp} goals={result['expected_goals']:.1f}")

        await db.commit()
        print(f"\n版本: {version}")

if __name__ == "__main__":
    asyncio.run(main())
