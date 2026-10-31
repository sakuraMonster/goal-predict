import asyncio, joblib, pandas as pd
from app.db.database import async_session
from app.db.models import Match
from app.predictor.features_a import FeatureEngineerA
from sqlalchemy import select

MODEL_DIR = "/app/models"

async def main():
    m = joblib.load(f"{MODEL_DIR}/model_a_wl.pkl")
    print("model_wl 特征数:", len(m.feature_name_))
    print("model_wl 特征:", m.feature_name_[:10], "...")
    async with async_session() as db:
        r = await db.execute(select(Match).where(Match.status == "finished", Match.home_score.isnot(None)).order_by(Match.kickoff_time.desc()).limit(1))
        match = r.scalar_one()
        feat = FeatureEngineerA(db)
        f = await feat.extract_features(match.id)
        print("extract_features 特征数:", f.shape[1])
        print("extract_features 列:", list(f.columns)[:10], "...")
        # 差异
        train = set(m.feature_name_)
        pred = set(f.columns)
        print("\n训练有、预测无:", sorted(train - pred))
        print("预测有、训练无:", sorted(pred - train))

asyncio.run(main())
