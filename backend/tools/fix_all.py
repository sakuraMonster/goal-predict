"""直接批量更新 fixture_id + 同步赔率 + 预测"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, date
from app.collector.pipeline import SyncPipeline
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select, func


# 从SM API手动验证的 fixture 映射
FIXTURE_MAP = {
    15478: 19635705,  # TPS图尔库 vs 玛丽港
    15479: 19635931,  # 赫根 vs 卡尔马
    15480: 19629617,  # 腓特烈斯塔 vs 桑纳菲
    15481: 19635702,  # 拉赫蒂 vs 查路
    15482: 19629614,  # 斯达 vs 维京
    15484: 19609657,  # 迈阿密国际 vs 哥伦布机员
    15485: 19609659,  # 温哥华白帽 vs 洛杉矶FC
    15487: 19609670,  # 芝加哥火焰 vs 夏洛特FC
    15488: 19609676,  # 圣路易斯城 vs 皇家盐湖城
    15489: 19609680,  # 洛城银河 vs 达拉斯
    15490: 19609682,  # 波特兰伐木工 vs 西雅图海湾人
    15491: 19635929,  # 布鲁马波 vs 马尔默
    15492: 19635933,  # IFK哥德堡 vs 代格福什
    15493: 19635704,  # 瓦萨 vs 国际图尔
    15497: 19629615,  # 莫尔德 vs 萨普斯堡
    15498: 19629612,  # 奥勒松 vs 特罗姆瑟
}


async def main():
    # Step 1: 直接更新 fixture_id
    print("=" * 60)
    print("Step 1: 批量更新 fixture_id")
    print("=" * 60)

    async with async_session() as db:
        for match_id, fx_id in FIXTURE_MAP.items():
            result = await db.execute(select(Match).where(Match.id == match_id))
            m = result.scalar_one_or_none()
            if m:
                home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
                away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
                m.sportmonks_fixture_id = fx_id
                print(f"  ✓ ID={match_id} {home} vs {away} → fixture_id={fx_id}")
            else:
                print(f"  ✗ ID={match_id} 不存在")
        await db.commit()
    print("  完成")

    # Step 2: 同步赔率
    print("\n" + "=" * 60)
    print("Step 2: 同步赔率")
    print("=" * 60)
    pipeline = SyncPipeline()
    await pipeline.sync_odds()

    # Step 3: 预测
    print("\n" + "=" * 60)
    print("Step 3: 批量预测")
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
        print(f"  待预测: {len(matches)} 场")

        pipeline_pred = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")
        created, updated, failed = 0, 0, 0

        for i, m in enumerate(matches):
            home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
            try:
                result_data = await pipeline_pred.predict(m.id)
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
                pred.model_version = version
                pred.kickoff_time = m.kickoff_time
                pred.league_id = m.league_id
                updated += 1
            else:
                pred = Prediction(
                    match_id=m.id, model_version=version,
                    home_prob=result_data["home_prob"], draw_prob=result_data["draw_prob"],
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
                    kickoff_time=m.kickoff_time, league_id=m.league_id,
                )
                db.add(pred)
                created += 1

            print(f"  OK ID={m.id} {home} vs {away} | {'新增' if created > 0 and created == (i+1-len([x for x in matches[:i] if x.id not in []])) else '更新'}")

            if (i + 1) % 10 == 0:
                await db.commit()

        await db.commit()
        print(f"\n  完成: 新增 {created}, 更新 {updated}, 失败 {failed}")

    print("\n全部完成!")


if __name__ == "__main__":
    asyncio.run(main())
