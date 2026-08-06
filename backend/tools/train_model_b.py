"""V4.12 重训练 Model B：使用 FeatureEngineerB 独立特征集"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np, pandas as pd, joblib
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_b import ModelB

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
os.makedirs(MODEL_DIR, exist_ok=True)


async def main():
    async with async_session() as db:
        print("加载训练数据...")
        result = await db.execute(
            select(Match).where(
                Match.home_score.isnot(None),
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.status == "finished",
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        print(f"已完成比赛: {len(matches)} 场")

        # 联赛分布
        from collections import Counter
        lc = Counter(m.league_id for m in matches)
        print("联赛分布:")
        for lid in sorted(lc, key=lambda x: lc[x], reverse=True):
            print(f"  league_id={lid}: {lc[lid]} 场")

        # 特征提取 —— 使用 FeatureEngineerB
        eng_b = FeatureEngineerB(db)
        X_list, y_goals = [], []
        failed = 0

        print(f"\n特征提取中 (FeatureEngineerB)...")
        for i, m in enumerate(matches):
            if (i + 1) % 500 == 0:
                print(f"  {i+1}/{len(matches)} (失败{failed})")
            try:
                feats = await eng_b.extract_features(m.id, sm_prediction=None)
                if feats.empty:
                    failed += 1
                    continue
                X_list.append(feats)
                y_goals.append(m.home_score + m.away_score)
            except Exception as e:
                failed += 1
                if failed <= 5:
                    print(f"  [错误] match_id={m.id}: {e}")

        X = pd.concat(X_list).fillna(0.0)
        y_goals = np.array(y_goals)

        print(f"\n有效样本: {len(X)}, 特征维度: {X.shape[1]}, 失败: {failed}")
        print(f"进球分布: 均值={y_goals.mean():.2f}, 0球={(y_goals==0).sum()}场({(y_goals==0).mean()*100:.1f}%)")

        # 80/20 按时间拆分
        split = int(len(X) * 0.8)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y_goals[:split], y_goals[split:]
        print(f"训练集: {len(X_train)} 场, 测试集: {len(X_test)} 场")

        # 训练 ModelB
        print("\n=== 训练 ModelB (Zero-Inflated Poisson, FeatureEngineerB) ===")
        model_b = ModelB()
        model_b.train(X_train, y_train)

        # 评估
        raw_lambda = model_b.model.predict(X_test)
        p_zero_mean = 0.0
        if model_b.zero_model is not None:
            try:
                zero_probs = model_b.zero_model.predict_proba(
                    model_b.zero_scaler.transform(X_test)
                )[:, 1]
                p_zero_mean = float(np.clip(zero_probs, 0.0, 0.35).mean())
            except Exception:
                pass
        pred_goals = raw_lambda * (1.0 - p_zero_mean)
        mae = np.abs(y_test - pred_goals).mean()
        mae_raw = np.abs(y_test - raw_lambda).mean()
        print(f"\n进球MAE: {mae:.4f} (ZIP调整) / {mae_raw:.4f} (原始λ)")
        print(f"零膨胀概率均值: {p_zero_mean:.4f}")

        # 测试集 R^2
        ss_res = np.sum((y_test - pred_goals) ** 2)
        ss_tot = np.sum((y_test - np.mean(y_test)) ** 2)
        r2 = 1 - ss_res / max(ss_tot, 1e-10)
        print(f"测试集 R²: {r2:.4f}")

        print(f"\n模型已保存到 {MODEL_DIR}/")
        print(f"  model_b.pkl + model_b_scaler.pkl")
        if model_b.zero_model:
            print(f"  model_b_zero.pkl + model_b_zero_scaler.pkl (ZIP启用)")
        print(f"  model_b_features.json (特征数: {len(model_b._feature_names)})")

asyncio.run(main())
