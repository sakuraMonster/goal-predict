"""模型C: 市场基线 Poisson —— 联赛参数包架构，支持联赛级定制规则"""
import numpy as np
from scipy.stats import poisson

class ModelC:
    """市场基线模型

    核心公式:
      λ = goal_line × calib × (1 + strength_adj + form_adj + drop_adj) × rule_factors

    联赛参数包: 每个联赛一个 dict，只覆写差异项，其余走 default
    """

    # ── 联赛参数包 ──
    # calib: 基于历史 ratio = avg(actual) / avg(goal_line)
    # strength_weight: 攻防偏离联赛均值的调整幅度
    # form_weight: 近期状态的调整幅度
    # drop_sensitivity: 盘口回落每单位对 λ 的衰减
    # low_score_*: 低分盘口特殊规则（韩K专属），goal_line≤阈值 且 drop≥阈值 → 额外衰减
    LEAGUE_PARAMS = {
        "default": {
            "calib": 0.95,
            "strength_weight": 0.12,
            "form_weight": 0.08,
            "drop_sensitivity": 0.05,
            # 低分规则（默认不启用: factor=1.0 即无效果）
            "low_score_enabled": False,
            "low_score_goal_line_max": 1.5,
            "low_score_drop_min": 1.0,
            "low_score_factor": 1.0,  # 1.0 = 不生效
        },
        "英超":   {"calib": 0.942},
        "西甲":   {"calib": 0.923},
        "德甲":   {"calib": 0.991},
        "意甲":   {"calib": 0.891},
        "法甲":   {"calib": 0.963},
        "日职联": {"calib": 0.880},
        "瑞典超": {"calib": 1.011},
        "芬超":   {"calib": 0.900},
        "挪超": {
            "calib": 1.022,
            "strength_weight": 0.14,
            # 博德闪耀 0-0: 盘口≤1.5 且回落≥1.0，市场看小球
            "low_score_enabled": True,
            "low_score_goal_line_max": 1.5,
            "low_score_drop_min": 1.0,
            "low_score_factor": 0.75,
        },
        "美职联": {
            "calib": 1.050,
            # 洛杉矶银河 0-0: 盘口≤1.5 且回落≥1.0，市场看小球
            "low_score_enabled": True,
            "low_score_goal_line_max": 1.5,
            "low_score_drop_min": 1.0,
            "low_score_factor": 0.75,
        },
        "巴甲":   {"calib": 0.912},
        "韩K": {
            "calib": 0.947,
            "strength_weight": 0.10,
            # 全北 0-0 规则: 盘口≤1.5 且回落≥1.0 → 市场强烈看小球
            "low_score_enabled": True,
            "low_score_goal_line_max": 1.5,
            "low_score_drop_min": 1.0,
            "low_score_factor": 0.75,
        },
    }

    def _get_params(self, league_name: str) -> dict:
        """获取联赛参数，联赛缺失时合并 default"""
        default = self.LEAGUE_PARAMS["default"]
        league = self.LEAGUE_PARAMS.get(league_name, {})
        return {**default, **league}

    def predict(self, features: dict, league_name: str = None) -> dict:
        p = self._get_params(league_name)

        # ── 1. 市场基线 ──
        goal_line = float(features.get("goal_line_market", 0) or 0)
        if goal_line < 0.5:
            goal_line = 2.5

        # ── 2. 球队攻防强度 ──
        home_gf = float(features.get("home_goals_avg", 0) or 1.3)
        away_gf = float(features.get("away_goals_avg", 0) or 1.2)
        total_attack = max(0.5, home_gf + away_gf)
        strength_adj = p["strength_weight"] * (total_attack / 2.5 - 1.0)

        # ── 3. 近期状态 ──
        home_gf6 = float(features.get("home_gf_avg_6", 0) or 0)
        away_gf6 = float(features.get("away_gf_avg_6", 0) or 0)
        form_adj = 0.0
        if home_gf6 > 0 or away_gf6 > 0:
            form_total = max(home_gf6, 0.3) + max(away_gf6, 0.3)
            form_adj = p["form_weight"] * (form_total / 2.5 - 1.0)

        # ── 4. 盘口回落信号（四档：微调/真实看小/诱盘逐级回调） ──
        goal_drop = float(features.get("goal_line_drop_from_peak", 0) or 0)
        if goal_drop >= 2.0:
            # >=2.0: 强诱盘，系数 0.50
            induce_coef = 0.50
            drop_adj = +induce_coef * min(goal_drop - 1.0, 1.0)
        elif goal_drop >= 1.5:
            # 1.5~2.0: 中诱盘，系数 0.30
            induce_coef = 0.30
            drop_adj = +induce_coef * (goal_drop - 1.0)
        elif goal_line > 2.0 and goal_drop > 1.0:
            # 1.0~1.5: 轻诱盘，系数 0.15（仅GL>2.0触发，低盘口回落为真实信号）
            induce_coef = 0.15
            drop_adj = +induce_coef * (goal_drop - 1.0)
        elif goal_drop >= 0.5:
            # 0.5~1.0: 真实看小球信号，正常负向衰减
            drop_adj = -p["drop_sensitivity"] * min(goal_drop, 2.0)
        else:
            # <0.5: 轻微波动，小幅负向
            drop_adj = -p["drop_sensitivity"] * min(goal_drop, 2.0)

        # ── 5. 计算基础 λ ──
        lambda_val = goal_line * p["calib"] * (1.0 + strength_adj + form_adj + drop_adj)

        # ── 6. 联赛定制规则: 低分盘口增强 ──
        if p["low_score_enabled"]:
            # 安全阀: drop>=2.0 为极端诱盘，不触发低分规则
            if goal_drop < 2.0 and goal_line <= p["low_score_goal_line_max"] and goal_drop >= p["low_score_drop_min"]:
                lambda_val *= p["low_score_factor"]

        lambda_val = max(0.5, min(6.0, lambda_val))

        # ── 7. Poisson 分布 ──
        goal_probs = [float(poisson.pmf(k, lambda_val)) for k in range(5)]
        goal_probs[4] = float(1.0 - poisson.cdf(3, lambda_val))
        total = sum(goal_probs)

        return {
            "expected_goals": round(lambda_val, 2),
            "raw_lambda": round(goal_line, 2),
            "goal_distribution": [round(p / total, 4) for p in goal_probs],
            "over_2_5_prob": round(float(1.0 - poisson.cdf(2, lambda_val)), 4),
            "zero_inflation_prob": 0.0,
            # 计算明细
            "detail": {
                "goal_line": round(goal_line, 2),
                "calib": p["calib"],
                "strength_adj": round(strength_adj, 4),
                "form_adj": round(form_adj, 4),
                "drop_adj": round(drop_adj, 4),
                "lambda_raw": round(goal_line * p["calib"] * (1.0 + strength_adj + form_adj + drop_adj), 4),
                "low_score_applied": p["low_score_enabled"] and goal_line <= p["low_score_goal_line_max"] and goal_drop >= p["low_score_drop_min"],
                "low_score_factor": p["low_score_factor"] if p["low_score_enabled"] else None,
                "home_goals_avg": round(home_gf, 2),
                "away_goals_avg": round(away_gf, 2),
                "home_gf_avg_6": round(home_gf6, 2),
                "away_gf_avg_6": round(away_gf6, 2),
                "goal_drop": round(goal_drop, 2),
                "league_name": league_name,
            },
        }
