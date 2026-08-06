"""为周日012-014匹配fixture + 同步赔率 + 预测"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

from datetime import date
from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import Match
from app.collector.pipeline import SyncPipeline
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select, func

TARGETS = [
    (15499, 2474, 1335),  # 布兰 vs 罗森博格
    (15500, 11126, 2925),  # 米拉索尔 vs 格雷米奥
    (15501, 2696, 303),    # 巴西国际 vs 科林蒂安
]


async def main():
    sm = SportMonksClient()

    # Step 1: 查询fixtures
    print("Step 1: 查询fixtures/date/2026-08-03")
    fixtures = await sm.get_fixtures_by_date("2026-08-03", includes="participants")
    print(f"  返回: {len(fixtures)} fixtures")

    # Step 2: 匹配
    print("\nStep 2: 匹配fixture ID")
    async with async_session() as db:
        for mid, sm1, sm2 in TARGETS:
            r = await db.execute(select(Match).where(Match.id == mid))
            m = r.scalar_one_or_none()
            if not m or m.sportmonks_fixture_id:
                continue
            found = None
            for fx in fixtures:
                pids = {p.get("id") for p in fx.get("participants", []) if isinstance(p, dict)}
                if sm1 in pids and sm2 in pids:
                    found = fx["id"]
                    break
            if found:
                m.sportmonks_fixture_id = found
                print(f"  ✓ ID={mid} {m.home_team_name} vs {m.away_team_name} → fixture_id={found}")
            else:
                h = m.home_team.name_zh if m.home_team else "?"
                a = m.away_team.name_zh if m.away_team else "?"
                print(f"  ✗ ID={mid} {h}({sm1}) vs {a}({sm2}) → 未找到")
                # 尝试单独搜索
                for fx in fixtures:
                    pids = {p.get("id") for p in fx.get("participants", []) if isinstance(p, dict)}
                    if sm1 in pids:
                        print(f"     {sm1} 在 fixture {fx['id']} pids={pids}")
        await db.commit()

    await sm.close()

    # Step 3: sync_odds
    print("\nStep 3: sync_odds")
    pipeline = SyncPipeline()
    await pipeline.sync_odds()

    # Step 4: predict
    print("\nStep 4: 预测")
    async with async_session() as db:
        r = await db.execute(select(Match).where(
            Match.home_team_id.isnot(None), Match.away_team_id.isnot(None),
            func.date(Match.kickoff_time) >= date(2026,8,1),
        ).order_by(Match.kickoff_time))
        matches = list(r.scalars().all())
        pp = PredictionPipeline(db)
        v = "20260801-fix"
        for m in matches:
            try:
                rd = await pp.predict(m.id)
            except Exception as e:
                print(f"  FAIL ID={m.id}: {e}")
                continue
            ex = await db.execute(select(__import__('app.db.models', fromlist=['Prediction']).Prediction).where(__import__('app.db.models', fromlist=['Prediction']).Prediction.match_id == m.id))
            p = ex.scalar_one_or_none()
            if p:
                p.home_prob = rd["home_prob"]; p.draw_prob = rd["draw_prob"]; p.away_prob = rd["away_prob"]
                p.handicap_home_prob = rd["handicap_home_prob"]; p.handicap_draw_prob = rd["handicap_draw_prob"]
                p.handicap_away_prob = rd["handicap_away_prob"]; p.expected_goals = rd["expected_goals"]
                p.over_2_5_prob = rd["over_2_5_prob"]; p.goal_distribution = rd["goal_distribution"]
                p.score_top5_json = rd["score_top5_json"]; p.confidence_level = rd["confidence_level"]
                p.is_cold_match = rd["is_cold_match"]; p.summary_text = rd["summary_text"]
                p.key_factors = rd.get("key_factors", ""); p.model_version = v
        await db.commit()
        print(f"  done")

    print("\n完成!")

asyncio.run(main())
