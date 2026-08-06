"""修正比赛team_id + fixture + 赔率 + 预测"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

from datetime import datetime, date
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.collector.pipeline import SyncPipeline
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select, func

# 比赛ID → (修正字段, 正确team_id, fixture_id)
FIXES = [
    (15494, "away_team_id", 167, 19635703),   # 坦佩雷山猫 → 埃尔维斯(id=167, SM=2617)
    (15495, "away_team_id", 150, 19635935),   # 厄尔格里特 → 奥尔格里特(id=150, SM=1870)
    (15496, "home_team_id", 181, None),        # 奥斯陆KFUM → 奥斯KFUM(id=181, SM=11914), fixture需要搜索
]


async def main():
    # Step 1: 修正 team_id + fixture_id
    print("Step 1: 修正 team_id")
    async with async_session() as db:
        for mid, field, correct_tid, fx_id in FIXES:
            r = await db.execute(select(Match).where(Match.id == mid))
            m = r.scalar_one_or_none()
            if m:
                setattr(m, field, correct_tid)
                if fx_id:
                    m.sportmonks_fixture_id = fx_id
                h = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
                a = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
                print(f"  ID={mid} {h} vs {a} | {field}={correct_tid} fx={fx_id}")
        await db.commit()

    # Step 2: sync_odds
    print("\nStep 2: sync_odds")
    pipeline = SyncPipeline()
    await pipeline.sync_odds()

    # Step 3: predict all
    print("\nStep 3: 预测")
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
                fail += 1; print(f"  FAIL ID={m.id}: {e}"); continue
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
        print(f"  {ok} 成功, {fail} 失败")

    # Step 4: 最终状态
    print("\nStep 4: 最终")
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
