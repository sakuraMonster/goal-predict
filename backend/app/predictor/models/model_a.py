"""模型A: LightGBM 多任务 — 胜平负 + 让球胜平负（自动加载已训练模型）"""
import os
import lightgbm as lgb
import joblib
import numpy as np
import pandas as pd

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "models")

CALIBRATION_TEMPERATURE = 1.0  # 温度缩放系数，1.0=不缩放（原2.5过于平滑导致区分度不足）


class ModelA:
    def __init__(self):
        wl_path = os.path.join(MODEL_DIR, "model_a_wl.pkl")
        hcp_path = os.path.join(MODEL_DIR, "model_a_hcp.pkl")
        
        self.model_wl = None
        if os.path.exists(wl_path) and os.path.getsize(wl_path) > 100:
            self.model_wl = joblib.load(wl_path)
        
        self.model_hcp = None
        if os.path.exists(hcp_path) and os.path.getsize(hcp_path) > 100:
            try:
                self.model_hcp = joblib.load(hcp_path)
            except Exception:
                pass  # 加载失败时 fallback 到 model_wl

    @staticmethod
    def _calibrate(probs: np.ndarray, temperature: float = CALIBRATION_TEMPERATURE) -> np.ndarray:
        """温度缩放校准：平滑极端概率，防止 0% 或 100% 预测"""
        p = np.maximum(probs, 1e-10)  # 防止 log(0)
        log_p = np.log(p) / temperature
        calibrated = np.exp(log_p)
        return calibrated / calibrated.sum()

    def predict(self, features: pd.DataFrame) -> dict:
        if self.model_wl is None:
            return {"home_prob": 0.33, "draw_prob": 0.34, "away_prob": 0.33,
                    "handicap_home_prob": 0.33, "handicap_draw_prob": 0.34, "handicap_away_prob": 0.33}

        # 确保特征列顺序与训练时一致
        if hasattr(self.model_wl, "feature_name_"):
            expected = self.model_wl.feature_name_
            features = features.reindex(columns=expected, fill_value=0.0)

        probs_wl = self.model_wl.predict_proba(features)[0]
        probs_wl = self._calibrate(probs_wl)
        probs_hcp = self.model_hcp.predict_proba(features)[0] if self.model_hcp else probs_wl
        probs_hcp = self._calibrate(probs_hcp)

        return {
            "home_prob": float(probs_wl[0]),
            "draw_prob": float(probs_wl[1]),
            "away_prob": float(probs_wl[2]),
            "handicap_home_prob": float(probs_hcp[0]),
            "handicap_draw_prob": float(probs_hcp[1]),
            "handicap_away_prob": float(probs_hcp[2]),
        }

    def train(self, X: pd.DataFrame, y_wl: np.ndarray, y_hcp: np.ndarray):
        self.model_wl = lgb.LGBMClassifier(
            objective="multiclass", num_class=3,
            n_estimators=200, learning_rate=0.05, max_depth=6,
            random_state=42, verbose=-1
        )
        self.model_wl.fit(X, y_wl)

        self.model_hcp = lgb.LGBMClassifier(
            objective="multiclass", num_class=3,
            n_estimators=200, learning_rate=0.05, max_depth=6,
            random_state=42, verbose=-1
        )
        self.model_hcp.fit(X, y_hcp)
