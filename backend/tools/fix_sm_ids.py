"""修复错误SM ID并重新匹配"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from app.collector.sportmonks.client import SportMonksClient
from app.collector.pipeline import SyncPipeline
from app.db.database import async_session
from app.db.models import Match, Team, Prediction
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select, func
from datetime import datetime, date


async def main():
    sm = SportMonksClient()

    # Step 1: 搜索正确的SM ID
    print("=" * 60)
    print("Step 1: 搜索正确的SM ID")
    print("=" * 60)

    fixes = {}

    # 坦佩雷山猫 → 应该是 Ilves (芬超球队，主场在Tampere)
    # fixture 19635703 显示对手是 2617
    print("\n搜索 Ilves:")
    results = await sm.search_teams("Ilves")
    for r in results[:3]:
        print(f"  id={r.get('id')} name={r.get('name')} country_id={r.get('country_id')}")
    # 验证 2617
    team_data = await sm.get_team_by_id(2617)
    if team_data:
        print(f"  SM 2617: {team_data.get('name')} (country_id={team_data.get('country_id')})")
    fixes[282] = (2617, "Ilves")  # 坦佩雷山猫 → Ilves

    # 厄尔格里特 → Örgryte IS (1870)
    print("\n验证 Örgryte (SM=1870):")
    team_data = await sm.get_team_by_id(1870)
    if team_data:
        print(f"  SM 1870: {team_data.get('name')} (country_id={team_data.get('country_id')})")
    fixes[852] = (1870, "Örgryte IS")

    # 奥斯陆KFUM → KFUM Oslo
    print("\n搜索 KFUM Oslo:")
    results = await sm.search_teams("KFUM Oslo")
    for r in results[:3]:
        print(f"  id={r.get('id')} name={r.get('name')} country_id={r.get('country_id')}")
    results2 = await sm.search_teams("KFUM")
    for r in results2[:3]:
        print(f"  id={r.get('id')} name={r.get('name')} country_id={r.get('country_id')}")

    # 先验证 fixture 19720915 的参与者
    print("\n验证 fixture 19720915:")
    fx = await sm.get_fixture_by_id(19720915, includes="participants")
    if fx:
        print(f"  name: {fx.get('name')}")
        print(f"  date: {fx.get('starting_at')}")
        for p in fx.get("participants", []):
            if isinstance(p, dict):
                print(f"  participant: id={p.get('id')} name={p.get('name')}")

    # 搜索 Kristiansund BK (挪威球队)
    print("\n搜索 Kristiansund BK:")
    results = await sm.search_teams("Kristiansund BK")
    for r in results[:3]:
        print(f"  id={r.get('id')} name={r.get('name')} country_id={r.get('country_id')}")

    # 查找 2791
    print("\n验证 SM 2791:")
    team_data = await sm.get_team_by_id(2791)
    if team_data:
        print(f"  SM 2791: {team_data.get('name')} (country_id={team_data.get('country_id')})")

    await sm.close()

    # Step 2: 更新错误SM ID
    print("\n" + "=" * 60)
    print("Step 2: 更新SM ID")
    print("=" * 60)

    async with async_session() as db:
        for team_id, (new_sm_id, new_name_en) in fixes.items():
            result = await db.execute(select(Team).where(Team.id == team_id))
            team = result.scalar_one_or_none()
            if team:
                old_sm = team.sportmonks_id
                old_name = team.name_en
                team.sportmonks_id = new_sm_id
                team.name_en = new_name_en
                team.needs_review = False
                team.review_reason = None
                print(f"  {team.name_zh}: SM {old_sm}→{new_sm_id}, name_en '{old_name}'→'{new_name_en}'")
        await db.commit()

    # Step 3: 重新匹配+赔率+预测
    print("\n" + "=" * 60)
    print("Step 3: 重新同步")
    print("=" * 60)
    pipeline = SyncPipeline()
    await pipeline.sync_odds()

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
        for m in matches:
            if m.id not in [15494, 15495, 15496]:
                continue
            home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
            try:
                result_data = await pipeline_pred.predict(m.id)
            except Exception as e:
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
            print(f"  OK ID={m.id} {home} vs {away}")

        await db.commit()

    print("\n完成!")


if __name__ == "__main__":
    asyncio.run(main())
