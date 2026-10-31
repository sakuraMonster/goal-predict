"""仅重训 model_a_hcp（让球模型），特征集与推理侧 FeatureEngineerA 输出一致（184 维）。

背景：此前 full_retrain.py 给让球模型额外加了 4 个 hcp_dev 偏离度特征（188 维），
但推理侧特征工程从未产出这些特征，导致 model_a_hcp 与 model_a_wl（184 维）维度不一致，
predict 时用 184 列去喂 188 维让球模型而抛错。本脚本按 184 维重训让球模型修复之。
"""
import asyncio
import os
import sys

# 容器内运行：确保能 import app 包（cwd 或 PYTHONPATH 可能不含 /app）
_CONTAINER_APP_ROOT = "/app"
if os.path.isdir(os.path.join(_CONTAINER_APP_ROOT, "app")):
    sys.path.insert(0, _CONTAINER_APP_ROOT)

import numpy as np
import pandas as pd
import joblib
import lightgbm as lgb
from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Match
from app.predictor.features_a import FeatureEngineerA

MODEL_DIR = os.environ.get("MODEL_DIR", "/app/models")


async def main():
    async with async_session() as db:
        eng = FeatureEngineerA(db)

        result = await db.execute(
            select(Match).where(
                Match.home_score.isnot(None),
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.handicap_line.isnot(None),
                Match.handicap_line != 0,
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        print(f"让球样本（有让球线且有比分）: {len(matches)} 场")

        Xh, yh = [], []
        for i, m in enumerate(matches):
            if (i + 1) % 1000 == 0:
                print(f"  特征提取 {i + 1}/{len(matches)}", flush=True)
            try:
                f = await eng.extract_features(m.id)
            except Exception as e:
                print(f"  跳过 match_id={m.id}（特征提取失败: {e}）", flush=True)
                continue
            if f.empty:
                continue
            Xh.append(f)
            adj = m.home_score + m.handicap_line
            yh.append(0 if adj > m.away_score else 1 if adj == m.away_score else 2)

        if not Xh:
            print("无可用让球样本，终止")
            return

        Xh_pd = pd.concat(Xh).fillna(0.0)
        yh_arr = np.array(yh)
        print(f"让球特征维度: {Xh_pd.shape[1]}，样本 {len(yh_arr)} 条")

        # 维度守护：必须与推理侧一致（当前为 184）
        if Xh_pd.shape[1] != 184:
            print(f"[WARN] 特征维度 {Xh_pd.shape[1]} != 184，请确认推理侧 FeatureEngineerA 输出维度", flush=True)

        sh = int(len(Xh_pd) * 0.8)
        model = lgb.LGBMClassifier(
            objective="multiclass", num_class=3,
            n_estimators=200, learning_rate=0.05, max_depth=6,
            random_state=42, verbose=-1,
        )
        model.fit(Xh_pd[:sh], yh_arr[:sh])

        from sklearn.metrics import accuracy_score
        acc = accuracy_score(yh_arr[sh:], model.predict(Xh_pd[sh:]))
        print(f"让球测试集准确率: {acc:.4f}（{len(yh_arr) - sh} 条）")

        dst = os.path.join(MODEL_DIR, "model_a_hcp.pkl")
        if os.path.exists(dst):
            import shutil
            shutil.copy2(dst, dst + ".pre_fix")
            print(f"已备份旧模型 → {dst}.pre_fix")
        joblib.dump(model, dst)
        loaded = joblib.load(dst)
        n = len(getattr(loaded, "feature_name_", []))
        print(f"已保存 {dst}，feature_name_ 维度 = {n}")


if __name__ == "__main__":
    asyncio.run(main())
