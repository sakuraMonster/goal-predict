"""V4 重训练：使用全量已完成比赛 + 新特征集，训练模型A+B"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np, pandas as pd, joblib
from datetime import datetime
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
os.makedirs(MODEL_DIR, exist_ok=True)


async def main():
    async with async_session() as db:
        # 1. 获取所有已完成的比赛（有比分的）
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

        # 2. 联赛分布
        from collections import Counter
        lc = Counter(m.league_id for m in matches)
        print("联赛分布:")
        for lid in sorted(lc, key=lambda x: lc[x], reverse=True):
            print(f"  league_id={lid}: {lc[lid]} 场")

        # 3. 特征提取
        eng = FeatureEngineer(db)
        X_list, y_wl, y_hcp, y_goals = [], [], [], []
        failed = 0

        print(f"\n特征提取中...")
        for i, m in enumerate(matches):
            if (i + 1) % 500 == 0:
                print(f"  {i+1}/{len(matches)} (失败{failed})")
            try:
                feats = await eng.extract_features(m.id)
                if feats.empty:
                    failed += 1
                    continue
                X_list.append(feats)
                # 胜平负标签: 0=主胜, 1=平, 2=客胜
                if m.home_score > m.away_score:
                    y_wl.append(0)
                elif m.home_score == m.away_score:
                    y_wl.append(1)
                else:
                    y_wl.append(2)
                # 让球标签
                adj = m.home_score + (m.handicap_line or 0)
                if adj > m.away_score:
                    y_hcp.append(0)
                elif adj == m.away_score:
                    y_hcp.append(1)
                else:
                    y_hcp.append(2)
                y_goals.append(m.home_score + m.away_score)
            except Exception as e:
                failed += 1
                if failed <= 5:
                    print(f"  [错误] match_id={m.id}: {e}")

        X = pd.concat(X_list).fillna(0.0)
        y_wl = np.array(y_wl)
        y_hcp = np.array(y_hcp)
        y_goals = np.array(y_goals)

        wc = Counter(y_wl)
        print(f"\n有效样本: {len(X)}, 特征维度: {X.shape[1]}, 失败: {failed}")
        print(f"标签分布: 主胜={wc[0]}({wc[0]/len(y_wl)*100:.1f}%) 平={wc[1]}({wc[1]/len(y_wl)*100:.1f}%) 客={wc[2]}({wc[2]/len(y_wl)*100:.1f}%)")

        # 4. 训练集/测试集拆分 (80/20)
        split = int(len(X) * 0.8)
        X_train, X_test = X[:split], X[split:]
        y_wl_train, y_wl_test = y_wl[:split], y_wl[split:]
        y_hcp_train, y_hcp_test = y_hcp[:split], y_hcp[split:]
        y_goals_train, y_goals_test = y_goals[:split], y_goals[split:]

        # 5. 训练 ModelA
        print("\n=== 训练 ModelA (LightGBM) ===")
        model_a = ModelA()
        model_a.train(X_train, y_wl_train, y_hcp_train)

        from sklearn.metrics import accuracy_score, classification_report
        pred_wl = model_a.model_wl.predict(X_test)
        pred_hcp = model_a.model_hcp.predict(X_test)
        acc_wl = accuracy_score(y_wl_test, pred_wl)
        acc_hcp = accuracy_score(y_hcp_test, pred_hcp)
        baseline = max(wc[0], wc[1], wc[2]) / len(y_wl)
        print(f"胜平负准确率: {acc_wl:.4f} (基线 {baseline:.3f})")
        print(f"让球准确率:   {acc_hcp:.4f}")

        # 特征重要性 Top 20
        print("\n=== ModelA 胜平负 Top 20 特征 ===")
        raw_imp = model_a.model_wl.booster_.feature_importance(importance_type="gain")
        total = raw_imp.sum()
        names = model_a.model_wl.feature_name_
        sorted_idx = np.argsort(raw_imp)[::-1]
        for idx in sorted_idx[:20]:
            pct = raw_imp[idx] / total * 100 if total > 0 else 0
            print(f"  {names[idx]}: {pct:.1f}%")

        # 6. 训练 ModelB (ZIP: Zero-Inflated Poisson, V4.11)
        print("\n=== 训练 ModelB (Zero-Inflated Poisson) ===")
        model_b = ModelB()
        # V4.11: 传入完整 y（含零进球），ModelB 内部训练 ZIP 的零膨胀子模型
        model_b.train(X_train, y_goals_train)
        
        # 评估：Poisson λ 和 ZIP 调整
        raw_lambda = model_b.model.predict(X_test)
        # ZIP 零膨胀概率（逐行预测太慢，用均值近似）
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
        mae = np.abs(y_goals_test - pred_goals).mean()
        mae_raw = np.abs(y_goals_test - raw_lambda).mean()
        print(f"进球MAE: {mae:.4f} (ZIP调整) / {mae_raw:.4f} (原始λ)")
        print(f"零膨胀概率均值: {p_zero_mean:.4f}")
        
        # 零进球比赛专项评估
        zero_mask_test = y_goals_test == 0
        if zero_mask_test.any():
            zero_mae_zip = np.abs(y_goals_test[zero_mask_test] - pred_goals[zero_mask_test]).mean()
            zero_mae_raw = np.abs(y_goals_test[zero_mask_test] - raw_lambda[zero_mask_test]).mean()
            print(f"零进球比赛 MAE: {zero_mae_zip:.4f} (ZIP) / {zero_mae_raw:.4f} (原始Poisson)")

        # 7. 保存模型
        joblib.dump(model_a.model_wl, os.path.join(MODEL_DIR, "model_a_wl.pkl"))
        joblib.dump(model_a.model_hcp, os.path.join(MODEL_DIR, "model_a_hcp.pkl"))
        # Model B 模型由 ModelB.train() 内部保存（含 ZIP 子模型）

        # 保存特征名（供预测时 reindex 使用）
        with open(os.path.join(MODEL_DIR, "feature_names.json"), "w") as f:
            json.dump(list(X.columns), f)

        print(f"\n模型已保存到 {MODEL_DIR}/")
        print(f"  model_a_wl.pkl (特征数: {len(model_a.model_wl.feature_name_)})")
        print(f"  model_a_hcp.pkl")
        print(f"  model_b.pkl + model_b_scaler.pkl + model_b_zero.pkl (ZIP)")
        print(f"  model_b_features.json")
        print(f"  feature_names.json")

asyncio.run(main())
