"""模型D: 分层 Dixon-Coles —— 球队攻防强度比率法 + 联赛基线 + 低分相关性矫正"""
import numpy as np
from scipy.stats import poisson


class ModelD:
    """分层进球预测模型

    核心逻辑:
      λ_home = league_baseline[league] × attack_ratio[home] × defense_ratio[away]
      λ_away = league_baseline[league] × attack_ratio[away] × defense_ratio[home]
      λ_total = λ_home + λ_away
      对于 (0,0)(1,0)(0,1)(1,1) 施加 Dixon-Coles ρ 矫正

    联赛基线: 历史场均进球 (from completed matches)
    攻防比率: team_stat / league_avg, 用近期状态微调
    """

    # 联赛场均进球基线 (based on completed match data)
    LEAGUE_BASELINE = {
        "英超": 2.75, "西甲": 2.71, "德甲": 3.07, "意甲": 2.47,
        "法甲": 2.87, "韩K": 2.72, "日职联": 2.37, "瑞典超": 2.87,
        "芬超": 3.07, "挪超": 3.18, "美职联": 3.10, "巴甲": 2.46,
    }
    DEFAULT_BASELINE = 2.70

    # Dixon-Coles 低分相关性参数 (典型值)
    RHO = 0.10

    def predict(self, features: dict, league_name: str = None) -> dict:
        """
        Args:
            features: 特征字典
            league_name: 联赛中文名
        Returns:
            同 ModelB 格式
        """
        # ── 1. 联赛基线 ──
        league_avg = self.LEAGUE_BASELINE.get(league_name, self.DEFAULT_BASELINE)
        league_home_avg = league_avg * 0.54  # 主场约 54% 进球
        league_away_avg = league_avg * 0.46

        # ── 2. 球队攻防统计 ──
        home_gf = max(float(features.get("home_goals_avg", 0) or 1.3), 0.3)
        home_ga = max(float(features.get("home_goals_against_avg", 0) or 1.2), 0.3)
        away_gf = max(float(features.get("away_goals_avg", 0) or 1.2), 0.3)
        away_ga = max(float(features.get("away_goals_against_avg", 0) or 1.3), 0.3)

        # 联赛参考值 (全局 ~2.7 / 2 = 1.35 per side)
        ref_gf = league_avg / 2.0
        ref_ga = league_avg / 2.0

        # ── 3. 近期状态修正 ──
        home_gf6 = float(features.get("home_gf_avg_6", 0) or 0)
        away_gf6 = float(features.get("away_gf_avg_6", 0) or 0)

        # 融合近期数据 (70% 赛季 + 30% 近6场)
        if home_gf6 > 0:
            home_gf = 0.7 * home_gf + 0.3 * home_gf6
        if away_gf6 > 0:
            away_gf = 0.7 * away_gf + 0.3 * away_gf6

        # ── 4. 计算攻防比率 (clamp 到合理范围) ──
        home_attack_ratio = np.clip(home_gf / max(ref_gf, 0.3), 0.4, 2.0)
        home_defense_ratio = np.clip(home_ga / max(ref_ga, 0.3), 0.4, 2.0)
        away_attack_ratio = np.clip(away_gf / max(ref_gf, 0.3), 0.4, 2.0)
        away_defense_ratio = np.clip(away_ga / max(ref_ga, 0.3), 0.4, 2.0)

        # ── 5. 计算 λ ──
        # λ_home = 主场基线 * 主队攻击力 * 客队防守力
        lambda_home = league_home_avg * home_attack_ratio * away_defense_ratio
        lambda_away = league_away_avg * away_attack_ratio * home_defense_ratio

        lambda_total = max(0.5, min(6.0, lambda_home + lambda_away))

        # ── 6. Poisson 分布 ──
        goal_probs = [float(poisson.pmf(k, lambda_total)) for k in range(5)]
        goal_probs[4] = float(1.0 - poisson.cdf(3, lambda_total))
        total = sum(goal_probs)

        # ── 7. Dixon-Coles 低分矫正: 对 0/1 球分布做 ρ 调整 ──
        # P(0) *= (1 - ρ), P(1) 略增
        rho = self.RHO
        if lambda_total < 3.5:  # 只在低分场景生效
            goal_probs[0] = goal_probs[0] * (1 - rho) + rho * 0.25
            # 重新归一化
            adj_total = sum(goal_probs)
            goal_probs = [p / adj_total for p in goal_probs]

        return {
            "expected_goals": round(lambda_total, 2),
            "raw_lambda": round(lambda_total, 2),
            "goal_distribution": [round(p, 4) for p in goal_probs],
            "over_2_5_prob": round(float(1.0 - poisson.cdf(2, lambda_total)), 4),
            "zero_inflation_prob": 0.0,
        }
