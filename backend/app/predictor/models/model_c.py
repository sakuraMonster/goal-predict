"""模型C: 市场+基本面动态博弈 Poisson —— 联赛参数包架构，支持联赛级定制规则"""
import numpy as np
from scipy.stats import poisson


class ModelC:
    """市场+基本面动态融合模型

    核心公式:
      λ = market_weight × λ_market + (1 - market_weight) × λ_fundamental

    market_weight 由三维度动态决定:
      ① 诱导强度（回落幅度、基本面背离、跨市场矛盾、波动）
      ② 市场可信度（庄家共识、盘口稳定、样本充足）
      ③ 盘口-基本面背离（开高/开低幅度）

    联赛参数包: 每个联赛一个 dict，只覆写差异项，其余走 default
    """

    # ── 联赛参数包 ──
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
            "low_score_factor": 1.0,
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
            "low_score_enabled": True,
            "low_score_goal_line_max": 1.5,
            "low_score_drop_min": 1.0,
            "low_score_factor": 0.75,
        },
        "美职联": {
            "calib": 1.050,
            "low_score_enabled": True,
            "low_score_goal_line_max": 1.5,
            "low_score_drop_min": 1.0,
            "low_score_factor": 0.75,
        },
        "巴甲":   {"calib": 0.912},
        "韩K": {
            "calib": 0.947,
            "strength_weight": 0.10,
            "low_score_enabled": True,
            "low_score_goal_line_max": 1.5,
            "low_score_drop_min": 1.0,
            "low_score_factor": 0.75,
        },
        # ── 新联赛（基于上赛季 SM 数据回归）──
        "葡超": {"calib": 1.040},   # 2024/2025: 341场, 场均2.60, actual/2.5=1.04
        "英冠": {"calib": 0.992},   # 2024/2025: 599场, 场均2.48, actual/2.5=0.99
        "荷乙": {"calib": 1.210},   # 2024/2025: 585场, 场均3.03, actual/2.5=1.21
        "德乙": {"calib": 1.225},   # 2024/2025: 368场, 场均3.06, actual/2.5=1.23
    }

    # ── 动态融合权重边界 ──
    MW_MIN = 0.30   # 市场权重最低（强诱导时）
    MW_MAX = 0.90   # 市场权重最高（正常市场）

    # ── 基本面联赛校准 ──
    # 30天回归发现: 美职联/瑞典超的基本面 λ_fund 系统性高估(GP), 韩K/挪超低估
    # 因子 <1.0 = 压缩基本面估值，>1.0 = 放大
    FUNDAMENTAL_CALIBRATION = {
        "美职联": 0.75,    # fund/GL=1.53 → 目标 ~1.15
        "瑞典超": 0.80,    # fund/GL=1.36 → 目标 ~1.09
        "韩K": 1.10,       # fund/GL=0.77 → 目标 ~0.85
        "挪超": 1.05,      # fund/GL=1.02, actual/GL=1.53 略补
    }

    def _get_params(self, league_name: str) -> dict:
        default = self.LEAGUE_PARAMS["default"]
        league = self.LEAGUE_PARAMS.get(league_name, {})
        return {**default, **league}

    # ═══════════════════════════════════════════════════════════════
    # 基本面预期进球 λ_fundamental
    # ═══════════════════════════════════════════════════════════════

    def _calc_fundamental_lambda(self, features: dict, league_name: str = None) -> float:
        """基于球队攻防数据计算基本面预期总进球

        公式:
          λ_home = league_home_avg × (home_attack_ratio) × (away_defense_weakness)
          λ_away = league_away_avg × (away_attack_ratio) × (home_defense_weakness)
          λ_fundamental = λ_home + λ_away

        攻击比率 = 赛季场均进球 / 联赛主场(或客场)均值
        防守漏洞 = 赛季场均失球 / 联赛客场(或主场)均值
        """
        home_gf = float(features.get("home_goals_avg", 0) or 1.3)
        away_gf = float(features.get("away_goals_avg", 0) or 1.2)
        home_ga = float(features.get("home_goals_against_avg", 0) or 1.2)
        away_ga = float(features.get("away_goals_against_avg", 0) or 1.3)
        ltg = float(features.get("league_avg_total_goals", 0) or 2.7)

        # 主场优势假定 55%
        league_home = ltg * 0.55
        league_away = ltg * 0.45

        # 攻击力 / 防守漏洞（相对联赛均值，clamp 防极端值）
        home_attack = np.clip(home_gf / max(league_home, 0.5), 0.6, 1.6)
        away_attack = np.clip(away_gf / max(league_away, 0.5), 0.6, 1.6)
        home_defense = np.clip(home_ga / max(league_away, 0.5), 0.6, 1.6)
        away_defense = np.clip(away_ga / max(league_home, 0.5), 0.6, 1.6)

        home_expected = league_home * home_attack * away_defense
        away_expected = league_away * away_attack * home_defense
        lambda_fund = home_expected + away_expected

        # 跨联赛场景（欧冠/杯赛/未知）：球队统计来自不同联赛，不可直接比较
        # 向市场值回归，降低基本面权重
        is_cross_league = league_name in (None, "未知", "欧冠") or not league_name
        if is_cross_league:
            goal_line = float(features.get("goal_line_market", 0) or 2.5)
            lambda_fund = 0.55 * max(0.5, goal_line) + 0.45 * lambda_fund

        # 联赛级基本面校准：修正系统性高估/低估
        fund_calib = self.FUNDAMENTAL_CALIBRATION.get(league_name, 1.0) if league_name else 1.0
        if fund_calib != 1.0:
            lambda_fund *= fund_calib

        return round(max(0.8, min(5.5, lambda_fund)), 2)

    # ═══════════════════════════════════════════════════════════════
    # 诱导评分 induce_score (0-1)
    # ═══════════════════════════════════════════════════════════════

    def _calc_induce_score(self, features: dict, goal_drop: float) -> float:
        """四维诱导评分

        维度:
          ① 盘口回落幅度 (权重 0.35) — 回落越大，诱盘嫌疑越大
          ② 基本面背离   (权重 0.25) — 基本面信号与市场方向相悖
          ③ 跨市场矛盾   (权重 0.20) — 欧赔/亚盘方向不一致
          ④ 盘口波动     (权重 0.20) — 盘口剧烈波动，可能被操控
        """
        goal_vol = float(features.get("goal_line_volatility", 0) or 0)
        odds_dir = float(features.get("odds_consensus_direction", 0) or 0)
        hcp_dir = float(features.get("handicap_consensus_direction", 0) or 0)
        fvm_div = float(features.get("fundamental_vs_market_divergence", 0) or 0)

        score = 0.0

        # ① 盘口回落信号 (0-0.35)
        if goal_drop >= 2.0:
            score += 0.35
        elif goal_drop >= 1.5:
            score += 0.25
        elif goal_drop >= 1.0:
            score += 0.15
        elif goal_drop >= 0.5:
            score += 0.05

        # ② 基本面背离 (0-0.25)
        # fvm_div > 0.15 表示基本面优势方向与赔率变动方向冲突
        if abs(fvm_div) > 0.15 and goal_drop > 0.5:
            score += 0.25
        elif abs(fvm_div) > 0.10 and goal_drop > 1.0:
            score += 0.25

        # ③ 跨市场矛盾 (0-0.20)
        # 欧赔和亚盘方向不一致（异号）= 庄家内部矛盾
        if odds_dir != 0 and hcp_dir != 0:
            if odds_dir * hcp_dir < 0:
                score += 0.20  # 方向相反，强烈矛盾
            elif abs(odds_dir) > 1 and abs(hcp_dir) > 1:
                score += 0.05  # 同向但幅度不一致，轻微

        # ④ 盘口波动 (0-0.20)
        if goal_vol > 0.75:
            score += 0.20
        elif goal_vol > 0.4:
            score += 0.10

        return min(1.0, score)

    # ═══════════════════════════════════════════════════════════════
    # 市场可信度 market_confidence (0-1)
    # ═══════════════════════════════════════════════════════════════

    def _calc_market_confidence(self, features: dict) -> float:
        """评估当前市场信号的可信度

        子维度:
          - 庄家共识度 (0.40): odds 离散度越低 → 共识越强
          - 盘口稳定性 (0.35): goal_line 波动越小 → 越稳定
          - 样本充足度 (0.25): 博彩公司越多 → 越可信
        """
        odds_disp = float(features.get("odds_dispersity", 0) or 0.1)
        goal_vol = float(features.get("goal_line_volatility", 0) or 0)
        bk_count = float(features.get("bookmaker_count", 0) or 5)

        disp_q = max(0.0, 1.0 - odds_disp / 0.5)
        stab_q = max(0.0, 1.0 - goal_vol / 1.0)
        bk_q = min(1.0, bk_count / 10.0)

        confidence = 0.40 * disp_q + 0.35 * stab_q + 0.25 * bk_q
        return round(min(1.0, max(0.15, confidence)), 4)

    # ═══════════════════════════════════════════════════════════════
    # 主预测
    # ═══════════════════════════════════════════════════════════════

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

        # ── 4. 盘口回落信号（真实看小信号，纯负向衰减） ──
        goal_drop = float(features.get("goal_line_drop_from_peak", 0) or 0)
        drop_adj = -p["drop_sensitivity"] * min(goal_drop, 2.0)

        # ── 5. λ_market（市场侧预测） ──
        lambda_market = goal_line * p["calib"] * (1.0 + strength_adj + form_adj + drop_adj)
        lambda_market = max(0.5, min(6.0, lambda_market))

        # ── 6. λ_fundamental（基本面侧预测） ──
        lambda_fundamental = self._calc_fundamental_lambda(features, league_name)

        # ── 7. 盘口-基本面背离 ──
        divergence = goal_line - lambda_fundamental

        # ── 8. 诱导评分 & 市场可信度 ──
        induce_score = self._calc_induce_score(features, goal_drop)
        market_confidence = self._calc_market_confidence(features)

        # ── 9. 动态权重融合 ──
        if induce_score > 0.5:
            # 强诱导场景：大幅降低市场权重
            market_weight = max(self.MW_MIN, 0.75 - induce_score)
        elif abs(divergence) > 0.75:
            # 盘口明显偏离基本面：市场/基本面各半
            market_weight = 0.35 + 0.15 * market_confidence
        else:
            # 正常场景：市场为主，置信度 + 背离惩罚
            div_penalty = min(0.20, abs(divergence) * 0.25)
            market_weight = 0.60 + 0.25 * market_confidence - div_penalty

        market_weight = round(min(self.MW_MAX, max(self.MW_MIN, market_weight)), 4)

        # 市场信号过度激进检测: 当市场偏离GL远超基本面时(同方向), 压低市场权重
        # 案例: 哈尔姆斯塔德 vs 天狼星, mkt偏+0.41 vs fund偏+0.15, mw过高导致MISS
        mkt_dev = lambda_market - goal_line
        fund_dev = lambda_fundamental - goal_line
        if abs(mkt_dev) > abs(fund_dev) * 1.5 and mkt_dev * fund_dev > 0:
            market_weight = min(market_weight, 0.40)

        # 韩K: 中度背离(0.75~1.5)时市场不可信，降低权重；极端背离(>1.5)时回归市场判断
        if league_name == "韩K" and 0.75 <= abs(divergence) <= 1.50:
            market_weight = min(market_weight, 0.25)

        # ── 10. 融合 λ ──
        lambda_val = market_weight * lambda_market + (1.0 - market_weight) * lambda_fundamental

        # ── 11. 联赛定制规则: 低分盘口增强 ──
        low_score_applied = False
        if p["low_score_enabled"]:
            if goal_drop < 2.0 and goal_line <= p["low_score_goal_line_max"] and goal_drop >= p["low_score_drop_min"]:
                lambda_val *= p["low_score_factor"]
                low_score_applied = True

        # ── 11.5 联赛专属规则 ──
        league_rule_applied = None

        if league_name == "挪超":
            # 挪超: 市场系统性低估进球(GP), avg_actual/GL=1.53, 0球率=0%, 5+球率=36%
            # 规则1: GL<2.25 时盘口过度看小，至少不低于盘口×1.1
            if goal_line < 2.25:
                lambda_val = max(lambda_val, goal_line * 1.10)
                league_rule_applied = "挪超-低盘口修正"
            # 规则2: 双方攻击力均>1.0 且 λ不太高时，挪超大概率爆进球
            if home_gf > 1.0 and away_gf > 1.0 and lambda_val < 3.5:
                lambda_val += 0.25
                league_rule_applied = "挪超-双方火力修正"

        elif league_name == "韩K":
            # 韩K: 盘口回落大+客队攻击强=市场诱小球，实际进球更多
            # Miss中回落均值0.82 vs Hit 0.45, 客攻Miss均值1.24 vs Hit 0.84
            # 安全阀: divergence<-0.5时基本面远高于市场, 回落是真实信号非诱导
            if goal_drop > 0.5 and away_gf > 1.0 and divergence > -0.5:
                lambda_val += 0.30
                league_rule_applied = "韩K-回落+客攻修正"
            # 韩K GL<=2.0 时λ过低，给盘口比例底线
            if goal_line <= 2.0 and lambda_val < goal_line * 1.15:
                lambda_val = max(lambda_val, goal_line * 1.15)
                league_rule_applied = league_rule_applied or "韩K-低盘口底线"

        elif league_name == "芬超":
            # 芬超: 低进球联赛(actual/GL=0.85)，GL≈2.5时市场系统性高估
            # 30天回归: 4/6 Miss为GL=2.5→1球(闷小球)，特征为有一方近6场积分为0
            # 安全阀: 双方赛季攻击力均>1.4时例外(HIT #3国际图尔vs火花打出3球)
            if abs(goal_line - 2.5) < 0.3 and divergence > -0.1:
                if not (home_gf > 1.4 and away_gf > 1.4):
                    lambda_val *= 0.85
                    league_rule_applied = "芬超-GL2.5闷小球修正"

        lambda_val = max(0.5, min(6.0, lambda_val))

        # ── 大球风险提示 ──
        high_goal_risk = None
        if league_name == "挪超":
            factors = ["联赛高方差(36%比赛5+球, 0%为0球)"]
            risk_level = "medium"
            # 中度回落(0.3~1.0): 反向指标，市场看小但挪超常爆
            if 0.3 < goal_drop <= 1.0:
                factors.append(f"中度盘口回落({goal_drop:.2f})为反向信号，大球风险升高")
                risk_level = "high"
            # 双方攻击力不弱 + GL适中
            if home_gf > 1.0 and away_gf > 0.9 and 2.0 <= goal_line <= 2.75:
                factors.append("双方攻击力不弱+GL适中，易于爆大球")
                if risk_level == "medium":
                    risk_level = "high"
            high_goal_risk = {"level": risk_level, "factors": factors}

        # ── 12. Poisson 分布 ──
        goal_probs = [float(poisson.pmf(k, lambda_val)) for k in range(5)]
        goal_probs[4] = float(1.0 - poisson.cdf(3, lambda_val))
        total = sum(goal_probs)

        return {
            "expected_goals": round(lambda_val, 2),
            "raw_lambda": round(goal_line, 2),
            "goal_distribution": [round(p / total, 4) for p in goal_probs],
            "over_2_5_prob": round(float(1.0 - poisson.cdf(2, lambda_val)), 4),
            "zero_inflation_prob": 0.0,
            "high_goal_risk": high_goal_risk,
            "detail": {
                "goal_line": round(goal_line, 2),
                "calib": p["calib"],
                "strength_adj": round(strength_adj, 4),
                "form_adj": round(form_adj, 4),
                "drop_adj": round(drop_adj, 4),
                "lambda_market": round(lambda_market, 2),
                "lambda_fundamental": round(lambda_fundamental, 2),
                "divergence": round(divergence, 2),
                "induce_score": round(induce_score, 4),
                "market_confidence": round(market_confidence, 4),
                "market_weight": round(market_weight, 4),
                "lambda_raw": round(lambda_val, 4),
                "low_score_applied": low_score_applied,
                "low_score_factor": p["low_score_factor"] if p["low_score_enabled"] else None,
                "league_rule_applied": league_rule_applied,
                "home_goals_avg": round(home_gf, 2),
                "away_goals_avg": round(away_gf, 2),
                "home_gf_avg_6": round(home_gf6, 2),
                "away_gf_avg_6": round(away_gf6, 2),
                "goal_drop": round(goal_drop, 2),
                "league_name": league_name,
            },
        }
