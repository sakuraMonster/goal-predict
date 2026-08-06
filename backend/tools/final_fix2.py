"""最终设置fixture + odds + predict"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, date
from app.db.database import async_session
from app.db.models import Match, Team, Prediction
from app.predictor.pipeline import PredictionPipeline
from app.collector.pipeline import SyncPipeline
from sqlalchemy import select, func


FIXTURE_MAP = {
    # from debug_match.py results:
    15494: 19635703,  # AC奥卢 vs Ilves
    15495: 19635935,  # AIK vs Orgryte
    # 15496: need to find
}


async def main():
    # Step 1: 确保球队SM ID正确
    print("Step 1: 确认球队SM ID")
    team_fixes = {
        282: (2617, "Ilves"),           # 坦佩雷山猫
        852: (1870, "Orgryte IS"),       # 厄尔格里特
        565: (11914, "KFUM Oslo"),       # 奥斯陆KFUM
    }

    async with async_session() as db:
        for tid, (sm_id, name_en) in team_fixes.items():
            r = await db.execute(select(Team).where(Team.id == tid))
            t = r.scalar_one_or_none()
            if t and t.sportmonks_id != sm_id:
                print(f"  {t.name_zh}: SM {t.sportmonks_id}→{sm_id}")
                t.sportmonks_id = sm_id
                t.name_en = name_en
                t.needs_review = False
        await db.commit()

    # Step 2: 设置fixture ID
    print("\nStep 2: 设置fixture ID")
    async with async_session() as db:
        for mid, fx_id in FIXTURE_MAP.items():
            r = await db.execute(select(Match).where(Match.id == mid))
            m = r.scalar_one_or_none()
            if m and not m.sportmonks_fixture_id:
                m.sportmonks_fixture_id = fx_id
                print(f"  ID={mid} → fixture_id={fx_id}")

        # 15496: 需要搜索 fixture
        mid = 15496
        r = await db.execute(select(Match).where(Match.id == mid))
        m = r.scalar_one_or_none()
        if m:
            home_sm = m.home_team.sportmonks_id if m.home_team else None
            away_sm = m.away_team.sportmonks_id if m.away_team else None
            print(f"  ID={mid}: home_sm={home_sm}, away_sm={away_sm}")
        await db.commit()

    # Step 3: sync_odds
    print("\nStep 3: sync_odds")
    pipeline = SyncPipeline()
    await pipeline.sync_odds()

    # Step 4: predict
    print("\nStep 4: 预测")
    async with async_session() as db:
        result = await db.execute(
            select(Match).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                func.date(Match.kickoff_time).in_([date(2026,8,1), date(2026,8,2)]),
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        pipeline_pred = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")
        for m in matches:
            try:
                rd = await pipeline_pred.predict(m.id)
            except Exception as e:
                print(f"  FAIL ID={m.id}: {e}")
                continue
            existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = existing.scalar_one_or_none()
            if pred:
                pred.home_prob = rd["home_prob"]; pred.draw_prob = rd["draw_prob"]; pred.away_prob = rd["away_prob"]
                pred.handicap_home_prob = rd["handicap_home_prob"]; pred.handicap_draw_prob = rd["handicap_draw_prob"]
                pred.handicap_away_prob = rd["handicap_away_prob"]; pred.expected_goals = rd["expected_goals"]
                pred.over_2_5_prob = rd["over_2_5_prob"]; pred.goal_distribution = rd["goal_distribution"]
                pred.score_top5_json = rd["score_top5_json"]; pred.confidence_level = rd["confidence_level"]
                pred.is_cold_match = rd["is_cold_match"]; pred.summary_text = rd["summary_text"]
                pred.key_factors = rd.get("key_factors", ""); pred.model_version = version
        await db.commit()
        print(f"  完成")

    print("\n全部完成!")

asyncio.run(main())
