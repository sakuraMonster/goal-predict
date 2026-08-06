"""模型B: Zero-Inflated Poisson — 进球数预测 + 比分推导（V4.11 支持零膨胀）"""
import os
import numpy as np
import pandas as pd
import joblib
from sklearn.linear_model import PoissonRegressor, LogisticRegression
from sklearn.preprocessing import StandardScaler
from scipy.stats import poisson

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "models")
FEATURE_NAMES_PATH = os.path.join(MODEL_DIR, "model_b_features.json")
ZERO_MODEL_PATH = os.path.join(MODEL_DIR, "model_b_zero.pkl")
ZERO_SCALER_PATH = os.path.join(MODEL_DIR, "model_b_zero_scaler.pkl")


class ModelB:
    def __init__(self):
        model_path = os.path.join(MODEL_DIR, "model_b.pkl")
        scaler_path = os.path.join(MODEL_DIR, "model_b_scaler.pkl")
        
        # 必须先加载 feature_names，scaler 维度检查依赖它
        self._feature_names = None
        if os.path.exists(FEATURE_NAMES_PATH):
            import json
            with open(FEATURE_NAMES_PATH, "r") as f:
                self._feature_names = json.load(f)
        
        self.model = joblib.load(model_path) if os.path.exists(model_path) else None
        self.scaler = None
        # 注意：当前模型训练时不使用 StandardScaler（train() 直接用原始特征 fit PoissonRegressor）
        # model_b_scaler.pkl 是旧版本遗留文件，维度不匹配，不可用
        
        # V4.11: Zero-Inflated 组件
        self.zero_model = joblib.load(ZERO_MODEL_PATH) if os.path.exists(ZERO_MODEL_PATH) else None
        self.zero_scaler = joblib.load(ZERO_SCALER_PATH) if os.path.exists(ZERO_SCALER_PATH) else None

    def predict(self, features: pd.DataFrame) -> dict:
        if self.model is None:
            return {
                "expected_goals": 2.5,
                "goal_distribution": [0.08, 0.20, 0.25, 0.21, 0.26],
                "over_2_5_prob": 0.50,
                "zero_inflation_prob": 0.08,
            }

        # 特征对齐：确保列顺序与训练时一致
        if self._feature_names:
            features = features.reindex(columns=self._feature_names, fill_value=0.0)

        # 特征标准化
        features_scaled = self._scale_features(features)

        # Poisson 回归预测 λ
        lambda_val = self.model.predict(features_scaled)[0]
        lambda_val = max(lambda_val, 0.1)
        lambda_val = min(lambda_val, 8.0)

        # V4.11: ZIP —— 零膨胀概率
        p_zero = 0.0
        if self.zero_model is not None and self.zero_scaler is not None:
            try:
                zero_features = self._scale_features(features, scaler=self.zero_scaler)
                p_zero = float(self.zero_model.predict_proba(zero_features)[0, 1])
                p_zero = np.clip(p_zero, 0.0, 0.25)  # V4.11b: 上限25%，避免过度零膨胀导致λ过低
            except Exception:
                p_zero = 0.0
        
        # 融合 ZIP 分布
        goal_probs = self._zip_distribution(lambda_val, p_zero)

        return {
            "expected_goals": float(lambda_val * (1.0 - p_zero)),  # 调整后期望进球
            "raw_lambda": float(lambda_val),
            "goal_distribution": goal_probs,
            "over_2_5_prob": float(1.0 - sum(goal_probs[:3])),  # P(0)+P(1)+P(2)的反面
            "zero_inflation_prob": p_zero,
        }

    def _scale_features(self, features: pd.DataFrame, scaler=None):
        """特征标准化（支持独立的 zero_scaler）"""
        s = scaler or self.scaler
        if s is None:
            return features.values
        
        # 复制，避免修改原始数据
        values = features.values.copy().astype(float)
        zero_mask = values[0] == 0.0
        if zero_mask.any():
            values[0, zero_mask] = s.mean_[zero_mask]
        return s.transform(values)

    def _zip_distribution(self, lam: float, p_zero: float) -> list:
        """Zero-Inflated Poisson 分布
        
        P(0) = p_zero + (1-p_zero) * Poisson(0|λ)
        P(k) = (1-p_zero) * Poisson(k|λ), k > 0
        """
        base = [float(poisson.pmf(k, lam)) for k in range(5)]
        base[4] = float(1.0 - poisson.cdf(3, lam))
        
        # ZIP 混合
        mix = [(1.0 - p_zero) * p for p in base]
        mix[0] += p_zero  # 零膨胀
        
        # 归一化
        total = sum(mix)
        return [p / total for p in mix]

    def train(self, X: pd.DataFrame, y: np.ndarray):
        """训练 Poisson + Zero-Inflated 模型"""
        # 1. 训练 Poisson 回归（主模型）
        self.model = PoissonRegressor(alpha=1e-3, max_iter=2000)  # V4.11: 增加迭代次数防止收敛警告
        self.model.fit(X, y)
        
        # 保存主模型
        model_path = os.path.join(MODEL_DIR, "model_b.pkl")
        joblib.dump(self.model, model_path)
        
        # 2. 训练零膨胀模型（逻辑回归预测是否零进球）
        y_zero = (y == 0).astype(int)
        zero_rate = y_zero.mean()
        # 仅当零进球比例>5%时才训练ZIP模型（避免样本不均衡）
        if zero_rate > 0.05:
            self.zero_scaler = StandardScaler()
            X_zero_scaled = self.zero_scaler.fit_transform(X)
            # 使用 class_weight 处理不均衡
            self.zero_model = LogisticRegression(
                class_weight='balanced',
                C=0.1,
                max_iter=1000,
                random_state=42,
            )
            self.zero_model.fit(X_zero_scaled, y_zero)
            
            train_acc = self.zero_model.score(X_zero_scaled, y_zero)
            # V4.12: ZIP 准确率阈值，低于70%不启用
            if train_acc > 0.70 and zero_rate > 0.05:
                # 保存 ZIP 模型
                joblib.dump(self.zero_model, ZERO_MODEL_PATH)
                joblib.dump(self.zero_scaler, ZERO_SCALER_PATH)
                print(f"[ModelB] ZIP zero-model trained: zero_rate={zero_rate:.3f}, train_acc={train_acc:.3f}")
            else:
                self.zero_model = None
                self.zero_scaler = None
                for p in [ZERO_MODEL_PATH, ZERO_SCALER_PATH]:
                    if os.path.exists(p):
                        os.remove(p)
                print(f"[ModelB] ZIP disabled: zero_rate={zero_rate:.3f}, train_acc={train_acc:.3f} < 0.70")
        
        # 保存特征列名
        import json
        with open(FEATURE_NAMES_PATH, "w") as f:
            json.dump(list(X.columns), f)
        self._feature_names = list(X.columns)
