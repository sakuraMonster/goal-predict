"""模型B: Poisson Regression — 进球数预测 + 比分推导"""
import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor
from scipy.stats import poisson


class ModelB:
    def __init__(self):
        self.model = None

    def predict(self, features: pd.DataFrame) -> dict:
        if self.model is None:
            return {
                "expected_goals": 2.5,
                "goal_distribution": [0.08, 0.20, 0.25, 0.21, 0.26],
                "over_2_5_prob": 0.50,
            }

        lambda_val = self.model.predict(features)[0]
        lambda_val = max(lambda_val, 0.1)

        goal_probs = [float(poisson.pmf(k, lambda_val)) for k in range(5)]
        goal_probs[4] = float(1.0 - poisson.cdf(3, lambda_val))

        return {
            "expected_goals": float(lambda_val),
            "goal_distribution": goal_probs,
            "over_2_5_prob": float(1.0 - poisson.cdf(2, lambda_val)),
        }

    def train(self, X: pd.DataFrame, y: np.ndarray):
        self.model = PoissonRegressor(alpha=1e-3, max_iter=500)
        self.model.fit(X, y)
