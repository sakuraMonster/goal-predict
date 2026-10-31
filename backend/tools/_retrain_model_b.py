"""只重训模型 B（进球数专属子集 + StandardScaler），不碰模型 A 与 TeamSeasonStats。

时间分离：训练用 TRAIN_BEFORE 之前的已结算比赛（limit 截断），
与回测窗口（近3个月）不重叠，避免训练/测试同批。

用法（服务器容器内）:
    docker exec -w /app ricking-backend-1 python _retrain_model_b.py
"""
import asyncio, os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np, pandas as pd, joblib
from sklearn.preprocessing import StandardScaler
from app.db.database import async_session
from app.db.models import Match
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_b import ModelB
from sqlalchemy import select

MODEL_DIR = "/app/models"
TRAIN_BEFORE = datetime(2026, 7, 23)  # 训练数据截止（回测窗口起点之前）
TRAIN_LIMIT = 6000                     # 最多取最近 N 场


async def main():
    async with async_session() as db:
        result = await db.execute(
            select(Match).where(
                Match.home_score.isnot(None),
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.kickoff_time < TRAIN_BEFORE,
            ).order_by(Match.kickoff_time.desc()).limit(TRAIN_LIMIT)
        )
        matches = list(result.scalars().all())
        print(f"训练样本（<{TRAIN_BEFORE.date()}）: {len(matches)} 场", flush=True)

        feat_b = FeatureEngineerB(db)
        Xb, yg = [], []
        failed = 0
        for i, m in enumerate(matches):
            if (i + 1) % 500 == 0:
                print(f"  提取 {i+1}/{len(matches)} (跳过{failed})", flush=True)
            try:
                fb = await feat_b.extract_features(m.id)
                if fb.empty:
                    failed += 1
                    continue
                Xb.append(fb[FeatureEngineerB.GOALS_FEATURE_COLS])
                yg.append(m.home_score + m.away_score)
            except Exception as e:
                failed += 1
                if failed <= 5:
                    print(f"  [跳过] match_id={m.id}: {e}", flush=True)

        X = pd.concat(Xb).fillna(0.0)
        y = np.array(yg)
        sp = int(len(X) * 0.8)
        print(f"\n子集维度={X.shape[1]}, 样本={len(X)}, 训练集={sp}, 测试集={len(X)-sp}, 跳过={failed}", flush=True)

        scaler_b = StandardScaler()
        X_scaled = scaler_b.fit_transform(X)
        X_scaled = pd.DataFrame(X_scaled, columns=X.columns)

        # 与 full_retrain 一致：剔除 0 球样本训练 Poisson 主模型
        mk = y[:sp] > 0
        mb = ModelB()
        mb.train(X_scaled[:sp][mk], y[:sp][mk])

        mae = np.abs(y[sp:] - mb.model.predict(X_scaled[sp:])).mean()
        print(f"进球MAE(测试集): {mae:.4f}", flush=True)

        # ModelB.train 已保存 model_b.pkl + model_b_features.json，这里补 scaler
        joblib.dump(scaler_b, os.path.join(MODEL_DIR, "model_b_scaler.pkl"))
        print(f"已保存: model_b.pkl / model_b_features.json / model_b_scaler.pkl (维度{X.shape[1]})", flush=True)


asyncio.run(main())
