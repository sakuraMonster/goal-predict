"""修复3支球队SM ID + 匹配fixture + 赔率 + 预测"""
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


TEAM_FIXES = {
    282: (2617, "Ilves"),         # 坦佩雷山猫
    852: (1870, "Orgryte IS"),     # 厄尔格里特
    565: (11914, "KFUM Oslo"),     # 奥斯陆KFUM
}

# 从之前debug_match.py验证过的fixture映射
FIXTURE_MAP = {
    15494: 19635703,  # AC奥卢 vs Ilves
    15495: 19635935,  # AIK vs Orgryte
}


async def main():
    # Step 1: 修复球队 SM ID（跳过已正确的）
    print("Step 1: 修复球队SM ID")
    async with async_session() as db:
        for tid, (sm, name_en) in TEAM_FIXES.items():
            r = await db.execute(select(Team).where(Team.id == tid))
            t = r.scalar_one_or_none()
            if t and t.sportmonks_id != sm:
                print(f"  {t.name_zh}: SM {t.sportmonks_id}→{sm}, name_en '{t.name_en}'→'{name_en}'")
                t.sportmonks_id = sm
                t.name_en = name_en
                t.needs_review = False
            else:
                print(f"  {t.name_zh}: SM={t.sportmonks_id} 已正确")
        await db.commit()

    # Step 2: 设置fixture ID
    print("\nStep 2: 设置fixture ID")
    async with async_session() as db:
        for mid, fx in FIXTURE_MAP.items():
            r = await db.execute(select(Match).where(Match.id == mid))
            m = r.scalar_one_or_none()
            if m and not m.sportmonks_fixture_id:
                m.sportmonks_fixture_id = fx
                print(f"  ID={mid} → fixture_id={fx}")
            else:
                print(f"  ID={mid}: fx={m.sportmonks_fixture_id if m else '?'}")
        await db.commit()

    # Step 3: sync_odds
    print("\nStep 3: sync_odds")
    pipeline = SyncPipeline()
    await pipeline.sync_odds()

    # Step 4: predict all 30
    print("\nStep 4: 预测全部30场")
    async with async_session() as db:
        r = await db.execute(select(Match).where(
            Match.home_team_id.isnot(None), Match.away_team_id.isnot(None),
            func.date(Match.kickoff_time).in_([date(2026,8,1), date(2026,8,2)]),
        ).order_by(Match.kickoff_time))
        matches = list(r.scalars().all())
        pp = PredictionPipeline(db)
        v = datetime.now().strftime("%Y%m%d-%H%M")
        ok, fail = 0, 0
        for m in matches:
            try:
                rd = await pp.predict(m.id)
            except Exception as e:
                fail += 1
                print(f"  FAIL ID={m.id}: {e}")
                continue
            ex = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            p = ex.scalar_one_or_none()
            if p:
                p.home_prob = rd["home_prob"]; p.draw_prob = rd["draw_prob"]; p.away_prob = rd["away_prob"]
                p.handicap_home_prob = rd["handicap_home_prob"]; p.handicap_draw_prob = rd["handicap_draw_prob"]
                p.handicap_away_prob = rd["handicap_away_prob"]; p.expected_goals = rd["expected_goals"]
                p.over_2_5_prob = rd["over_2_5_prob"]; p.goal_distribution = rd["goal_distribution"]
                p.score_top5_json = rd["score_top5_json"]; p.confidence_level = rd["confidence_level"]
                p.is_cold_match = rd["is_cold_match"]; p.summary_text = rd["summary_text"]
                p.key_factors = rd.get("key_factors", ""); p.model_version = v
            ok += 1
        await db.commit()
        print(f"  完成: {ok} 成功, {fail} 失败")

    # Step 5: 最终状态
    print("\nStep 5: 最终状态")
    async with async_session() as db:
        for d in [date(2026,8,1), date(2026,8,2)]:
            r = await db.execute(select(Match).where(func.date(Match.kickoff_time) == d).order_by(Match.kickoff_time))
            ms = list(r.scalars().all())
            matched = sum(1 for m in ms if m.sportmonks_fixture_id)
            print(f"  {d}: {matched}/{len(ms)} 有SM fixture")
            for m in ms:
                if not m.sportmonks_fixture_id:
                    h = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
                    a = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
                    print(f"    ID={m.id} {h} vs {a}")

    print("\n完成!")

asyncio.run(main())
