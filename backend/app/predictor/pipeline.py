"""预测 Pipeline：整合模型A+B + 比分推导 + 报告生成"""
import numpy as np
from scipy.stats import poisson
from sqlalchemy.ext.asyncio import AsyncSession
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB


class PredictionPipeline:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.feature_engineer = FeatureEngineer(db)
        self.model_a = ModelA()
        self.model_b = ModelB()

    async def predict(self, match_id: int) -> dict:
        features = await self.feature_engineer.extract_features(match_id)
        if features.empty:
            return self._empty_result()

        result_a = self.model_a.predict(features)
        result_b = self.model_b.predict(features)

        score_top5 = self._derive_scores(
            result_a["home_prob"], result_a["draw_prob"], result_a["away_prob"],
            result_b["expected_goals"]
        )

        is_cold = self._is_cold_match(result_a)
        confidence = self._calc_confidence(result_a, is_cold)

        return {
            **result_a, **result_b,
            "score_top5_json": score_top5,
            "is_cold_match": is_cold,
            "confidence_level": confidence,
            "summary_text": self._generate_summary(result_a, result_b, is_cold),
        }

    def _derive_scores(self, home_p, draw_p, away_p, expected_goals):
        scores = []
        max_goals = min(int(expected_goals) + 3, 6)
        for hg in range(max_goals + 1):
            for ag in range(max_goals + 1):
                if hg + ag == 0:
                    continue
                p = (poisson.pmf(hg + ag, expected_goals) *
                     (home_p if hg > ag else draw_p if hg == ag else away_p))
                result = "home" if hg > ag else "draw" if hg == ag else "away"
                scores.append({"score": f"{hg}:{ag}", "prob": round(float(p), 4), "result": result})
        scores.sort(key=lambda x: x["prob"], reverse=True)
        return scores[:5]

    def _is_cold_match(self, result_a: dict) -> bool:
        max_prob = max(result_a["home_prob"], result_a["draw_prob"], result_a["away_prob"])
        return max_prob < 0.40

    def _calc_confidence(self, result_a: dict, is_cold: bool) -> str:
        max_prob = max(result_a["home_prob"], result_a["draw_prob"], result_a["away_prob"])
        if is_cold or max_prob < 0.45:
            return "low"
        if max_prob > 0.60:
            return "high"
        return "medium"

    def _generate_summary(self, result_a, result_b, is_cold) -> str:
        max_label = max(
            [("主胜", result_a["home_prob"]), ("平局", result_a["draw_prob"]), ("客胜", result_a["away_prob"])],
            key=lambda x: x[1]
        )
        summary = f"模型预测倾向{max_label[0]}（概率{max_label[1]*100:.1f}%），预期总进球{result_b['expected_goals']:.1f}球。"
        if is_cold:
            summary += " 注意：本场被标记为冷门预警赛事，建议谨慎参考。"
        return summary

    def _empty_result(self) -> dict:
        return {
            "home_prob": 0.33, "draw_prob": 0.34, "away_prob": 0.33,
            "handicap_home_prob": 0.33, "handicap_draw_prob": 0.34, "handicap_away_prob": 0.33,
            "expected_goals": 2.5, "goal_distribution": [0.08,0.20,0.25,0.21,0.26],
            "over_2_5_prob": 0.50, "score_top5_json": [],
            "is_cold_match": True, "confidence_level": "low",
            "summary_text": "数据不足，无法生成预测报告。"
        }
