"""完整数据管道：赔率同步 → 球队数据更新 → 批量预测"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime
from app.collector.pipeline import SyncPipeline
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select, func


async def step1_sync_odds():
    """Step 1: 球队匹配 + 赔率同步"""
    print("\n" + "="*60)
    print("  Step 1: 赔率同步 (含球队匹配)")
    print("="*60)
    pipeline = SyncPipeline()
    await pipeline.sync_odds()
    print("  完成")


async def step2_sync_teams():
    """Step 2: 球队数据更新（统计、交锋、状态）"""
    print("\n" + "="*60)
    print("  Step 2: 球队数据更新 (近期状态 + 历史交锋)")
    print("="*60)
    pipeline = SyncPipeline()
    await pipeline.sync_team_info()
    print("  完成")


async def step3_predict_today_tomorrow():
    """Step 3: 对今天和明天的比赛执行预测"""
    print("\n" + "="*60)
    print("  Step 3: 批量预测")
    print("="*60)

    async with async_session() as db:
        # 只预测今天和明天的比赛（有球队匹配的）
        from datetime import date
        today = date(2026, 8, 1)
        tomorrow = date(2026, 8, 2)
        result = await db.execute(
            select(Match).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                func.date(Match.kickoff_time).in_([today, tomorrow]),
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        print(f"  待预测比赛: {len(matches)} 场")

        pipeline = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")
        created, updated, failed = 0, 0, 0

        for i, m in enumerate(matches):
            try:
                result_data = await pipeline.predict(m.id)
            except Exception as e:
                failed += 1
                home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
                away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
                print(f"  预测失败 ID={m.id} {home} vs {away}: {e}")
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
                    kickoff_time=m.kickoff_time,
                    league_id=m.league_id,
                )
                db.add(pred)
                created += 1

            if (i + 1) % 10 == 0:
                await db.commit()
                print(f"  进度: {i+1}/{len(matches)}")

        await db.commit()
        print(f"  完成: 新增 {created}, 更新 {updated}, 失败 {failed}")
        print(f"  版本: {version}")


async def main():
    print("\n" + "#"*60)
    print("#  完整数据管道执行")
    print(f"#  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("#"*60)

    # Step 1: 赔率同步
    await step1_sync_odds()

    # Step 2: 球队数据更新
    await step2_sync_teams()

    # Step 3: 批量预测
    await step3_predict_today_tomorrow()

    print("\n" + "#"*60)
    print("#  全部完成!")
    print("#"*60)


if __name__ == "__main__":
    asyncio.run(main())
