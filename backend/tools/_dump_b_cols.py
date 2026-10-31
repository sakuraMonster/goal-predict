"""打印 FeatureEngineerA vs FeatureEngineerB 完整列名与差异（用于确定进球数子集）"""
import asyncio, json
from app.db.database import async_session
from app.db.models import Match
from app.predictor.features_a import FeatureEngineerA
from app.predictor.features_b import FeatureEngineerB
from sqlalchemy import select


async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Match).where(
                Match.status == "finished", Match.home_score.isnot(None)
            ).order_by(Match.kickoff_time.desc()).limit(1)
        )
        m = r.scalars().first()
        print("match_id", m.id, m.kickoff_time)
        fa = FeatureEngineerA(db)
        fb = FeatureEngineerB(db)
        a = await fa.extract_features(m.id)
        b = await fb.extract_features(m.id)
        print("A cols", a.shape[1])
        print("B cols", b.shape[1])
        acols = set(a.columns)
        bcols = set(b.columns)
        print("\n[A有B无]", json.dumps(sorted(acols - bcols), ensure_ascii=False))
        print("\n[B有A无]", json.dumps(sorted(bcols - acols), ensure_ascii=False))
        print("\n[B 完整列]", json.dumps(list(b.columns), ensure_ascii=False))


asyncio.run(main())
