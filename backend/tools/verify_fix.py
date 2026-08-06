"""重置错误球队 + 重新运行完整管道"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, date
from app.db.database import async_session
from app.db.models import Match, Team, Prediction
from app.collector.pipeline import SyncPipeline
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select, func


async def main():
    # Step 0: 重置之前手动修复的3个球队SM ID（让新代码验证）
    print("=" * 60)
    print("Step 0: 重置球队映射（让修复后的代码重新匹配）")
    print("=" * 60)
    async with async_session() as db:
        for tid, name_zh in [(282, "坦佩雷山猫"), (852, "厄尔格里特"), (565, "奥斯陆KFUM")]:
            r = await db.execute(select(Team).where(Team.id == tid))
            t = r.scalar_one_or_none()
            if t:
                print(f"  {name_zh}: 清除 SM={t.sportmonks_id}, name_en='{t.name_en}'")
                t.sportmonks_id = None
                t.name_en = None
                t.needs_review = True
        await db.commit()

    # Step 1: match_to_sportmonks (现在应该正确匹配)
    print("\n" + "=" * 60)
    print("Step 1: match_to_sportmonks (修复后)")
    print("=" * 60)
    pipeline = SyncPipeline()
    await pipeline.match_to_sportmonks()

    # Step 2: sync_odds
    print("\n" + "=" * 60)
    print("Step 2: sync_odds")
    print("=" * 60)
    await pipeline.sync_odds()

    # Step 3: 检查当前状态
    print("\n" + "=" * 60)
    print("Step 3: 当前状态")
    print("=" * 60)
    async with async_session() as db:
        for d in [date(2026,8,1), date(2026,8,2)]:
            result = await db.execute(
                select(Match).where(func.date(Match.kickoff_time) == d).order_by(Match.kickoff_time)
            )
            matches = list(result.scalars().all())
            matched = sum(1 for m in matches if m.sportmonks_fixture_id)
            print(f"  {d}: {matched}/{len(matches)} 有SM fixture")

    # Step 4: 预测
    print("\n" + "=" * 60)
    print("Step 4: 预测")
    print("=" * 60)
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
        ok, fail = 0, 0
        for m in matches:
            try:
                result_data = await pipeline_pred.predict(m.id)
            except Exception as e:
                fail += 1
                print(f"  FAIL ID={m.id}: {e}")
                continue
            existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = existing.scalar_one_or_none()
            if pred:
                pred.home_prob = result_data["home_prob"]; pred.draw_prob = result_data["draw_prob"]
                pred.away_prob = result_data["away_prob"]; pred.handicap_home_prob = result_data["handicap_home_prob"]
                pred.handicap_draw_prob = result_data["handicap_draw_prob"]; pred.handicap_away_prob = result_data["handicap_away_prob"]
                pred.expected_goals = result_data["expected_goals"]; pred.over_2_5_prob = result_data["over_2_5_prob"]
                pred.goal_distribution = result_data["goal_distribution"]; pred.score_top5_json = result_data["score_top5_json"]
                pred.confidence_level = result_data["confidence_level"]; pred.is_cold_match = result_data["is_cold_match"]
                pred.summary_text = result_data["summary_text"]; pred.key_factors = result_data.get("key_factors", "")
                pred.model_version = version
            ok += 1
            if ok % 10 == 0:
                await db.commit()
        await db.commit()
        print(f"  完成: 成功 {ok}, 失败 {fail}")

    # Step 5: 最终状态
    print("\n" + "=" * 60)
    print("最终: 无SM fixture的比赛")
    print("=" * 60)
    async with async_session() as db:
        result = await db.execute(
            select(Match).where(
                Match.sportmonks_fixture_id.is_(None),
                func.date(Match.kickoff_time).in_([date(2026,8,1), date(2026,8,2)]),
            ).order_by(Match.kickoff_time)
        )
        remaining = list(result.scalars().all())
        if remaining:
            for m in remaining:
                h = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
                a = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
                print(f"  ID={m.id} {h} vs {a} | h_sm={m.home_team.sportmonks_id if m.home_team else '?'} a_sm={m.away_team.sportmonks_id if m.away_team else '?'}")
        else:
            print("  全部已匹配!")

    print("\n完成!")

asyncio.run(main())
