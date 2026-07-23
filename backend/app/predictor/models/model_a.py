"""模型A: LightGBM 多任务 — 胜平负 + 让球胜平负"""
import lightgbm as lgb
import numpy as np
import pandas as pd


class ModelA:
    def __init__(self):
        self.model_wl = None   # 胜平负分类器
        self.model_hcp = None  # 让球胜平负分类器

    def predict(self, features: pd.DataFrame) -> dict:
        if self.model_wl is None:
            return {"home_prob": 0.33, "draw_prob": 0.34, "away_prob": 0.33,
                    "handicap_home_prob": 0.33, "handicap_draw_prob": 0.34, "handicap_away_prob": 0.33}

        probs_wl = self.model_wl.predict_proba(features)[0]
        probs_hcp = self.model_hcp.predict_proba(features)[0] if self.model_hcp else probs_wl

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
