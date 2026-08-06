"""
Model B 专用特征工程 —— 进球/Poisson预测方向，独立权重参数

从 FeatureEngineer（features.py）完整派生，继承 BaseDataFetcher 的数据库查询能力。
所有硬编码数值参数均提取为类级别常量，方便 Model B 独立调参。
"""
import pandas as pd
import numpy as np
import math
from collections import Counter
from datetime import timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, or_, cast, Integer
from app.db.models import Match, TeamSeasonStats, HeadToHead, OddsSnapshot, Injury
from app.predictor.features_base import BaseDataFetcher


class FeatureEngineerB(BaseDataFetcher):
    """Model B 专用特征工程 —— 进球/Poisson预测方向，独立权重参数"""

    # ============================================================
    # 通用默认值
    # ============================================================
    DEFAULT_WIN_RATE = 0.33
    DEFAULT_DRAW_RATE = 0.25
    DEFAULT_HOME_GOALS = 1.5
    DEFAULT_AWAY_GOALS = 1.2
    DEFAULT_PPG = 1.5
    DEFAULT_CLEAN_SHEET_RATE = 0.2
    DEFAULT_XG = 0.0
    POINTS_PER_WIN = 3
    POINTS_PER_DRAW = 1
    DENOM_MIN = 0.01
    DENOM_MIN_0_1 = 0.1
    DENOM_MIN_1 = 1
    XG_THRESHOLD = 0.01
    MATH_E = math.e

    # ============================================================
    # 类别 A: 球队基础战力
    # ============================================================
    TEAM_STRENGTH_DEFAULT_HWR = 0.45
    TEAM_STRENGTH_MATCH_INTENSITY_EXP = 0.5

    # ============================================================
    # 类别 A+: 上下文特征
    # ============================================================
    CONTEXT_DEFAULT_LEAGUE_ID = 0.0
    CONTEXT_MONTHS_EARLY = (8, 9)
    CONTEXT_MONTHS_MID = (10, 11, 12, 1)
    CONTEXT_SEASON_STAGE_EARLY = 0.0
    CONTEXT_SEASON_STAGE_MID = 1.0
    CONTEXT_SEASON_STAGE_LATE = 2.0
    CONTEXT_WEEKDAY_THRESHOLD = 5
    CONTEXT_DEFAULT_SEASON_STAGE = 1.0
    CONTEXT_DEFAULT_GAMES_PLAYED = 0.0
    CONTEXT_SEASON_TOTAL_DEFAULT = 38
    CONTEXT_SEASON_TOTAL_MIN = 10
    CONTEXT_PROGRESS_RATIO_MAX = 1.0
    CONTEXT_DEFAULT_POINTS_DIFF = 0.0
    CONTEXT_STAGE_FACTOR_EARLY = 0.3
    CONTEXT_STAGE_FACTOR_MID = 0.7
    CONTEXT_STAGE_FACTOR_LATE = 1.0
    CONTEXT_STAGE_THRESHOLD_EARLY = 0.5
    CONTEXT_STAGE_THRESHOLD_MID = 1.5

    # ============================================================
    # 动机压力
    # ============================================================
    MOTIVATION_TOTAL_GAMES = 38
    MOTIVATION_DEFAULT = 0.3
    MOTIVATION_CHAMPION_BASE = 0.6
    MOTIVATION_CHAMPION_RANGE = 0.4
    MOTIVATION_EUROPE_BASE = 0.4
    MOTIVATION_EUROPE_RANGE = 0.3
    MOTIVATION_MID_BASE = 0.5
    MOTIVATION_MID_DECAY = 0.4
    MOTIVATION_MID_MIN = 0.1
    MOTIVATION_RELEGATION_BASE = 0.5
    MOTIVATION_RELEGATION_RANGE = 0.5
    MOTIVATION_CHAMPION_PPG = 2.0
    MOTIVATION_EUROPE_PPG = 1.6
    MOTIVATION_MID_PPG = 1.0

    # ============================================================
    # 类别 B: H2H 交锋
    # ============================================================
    H2H_HAS_DATA = 1.0
    H2H_NO_DATA = 0.0
    H2H_DEFAULT_XG_HOME = 1.35
    H2H_DEFAULT_XG_AWAY = 1.15
    H2H_DEFAULT_XG_DIFF = 0.2
    H2H_DEFAULT_SHOTS_HOME = 13.0
    H2H_DEFAULT_SHOTS_AWAY = 11.0
    H2H_DEFAULT_SHOTS_RATIO = 0.55
    # 韩K(league_id=6)射门→进球转化率低于全球均值，H2H shots 特征需衰减
    H2H_SHOTS_LEAGUE_ATTENUATION = {6: 0.80}
    H2H_DEFAULT_POSSESSION = 50.0
    H2H_DEFAULT_DANGER_HOME = 45.0
    H2H_DEFAULT_DANGER_AWAY = 40.0
    H2H_DEFAULT_GOALS_HOME = 1.5
    H2H_DEFAULT_GOALS_AWAY = 1.3
    H2H_DEFAULT_WIN_RATE = 0.33
    H2H_DEFAULT_DRAW_RATE = 0.34
    H2H_DEFAULT_STATS_AVAILABLE = 0.0
    H2H_BAYES_PRIOR_STRENGTH = 2.0
    H2H_BAYES_XG_PRIOR_STRENGTH = 0.5
    H2H_POSS_CENTER = 50.0
    H2H_POSS_COMPRESSION = 0.2
    H2H_POINTS_WIN = 3
    H2H_POINTS_DRAW = 1

    # ============================================================
    # 类别 C: 赔率 - 1X2 欧赔
    # ============================================================
    ODDS_DEFAULT_PROB = 1.0 / 3.0
    ODDS_DEFAULT_VAL = 0.0
    ODDS_TIME_DEPTH = 14.0
    ODDS_MIN_SAMPLES_STD = 2
    ODDS_DENOM_MIN = 0.01
    ODDS_HCP_SIGN_THRESHOLD = 0.01
    ODDS_HCP_SIGN_NEG_THRESHOLD = -0.01
    ODDS_DEFAULT_ODDS_HOME = 2.0
    ODDS_DEFAULT_ODDS_AWAY = 2.0

    # ============================================================
    # 类别 C: 亚盘
    # ============================================================
    HCP_BEST_BALANCE_INIT = float("inf")

    # ============================================================
    # 类别 C: 大小球
    # ============================================================
    OU_DECLINE_WINDOW_HOURS = 6
    OU_DECLINE_MIN_HOURS = 0.5
    OU_SECONDS_PER_HOUR = 3600
    # V4.12: 大小球盘口合理范围 —— 过滤非标准盘口（角球盘等特殊市场混入）
    GOAL_LINE_MIN_VALID = 1.5    # 标准全场大小球下限
    GOAL_LINE_MAX_VALID = 3.5    # 标准全场大小球上限
    GOAL_LINE_MIN_COUNT = 2      # 最少需有多少 bookmaker 共识才采纳该盘口

    # ============================================================
    # 类别 C+: 偏离度
    # ============================================================
    DEV_DEFAULT_DRAW = 0.25
    DEV_DEFAULT_PROB = 1.0 / 3.0

    # ============================================================
    # 类别 C++: JC 与市场偏离
    # ============================================================
    JC_DIFF_DEFAULT = 0.0

    # ============================================================
    # 类别 E: 近期状态
    # ============================================================
    FORM_DEFAULT = 0.0
    FORM_PTS_WIN = 3
    FORM_PTS_DRAW = 1
    FORM_WINDOW_6 = 6
    FORM_WINDOW_10 = 10
    FORM_GF_WINSORIZE_CAP = 3.0
    FORM_GF_PRIOR = 1.3   # V4.11: 降低先验值，增加特征方差
    FORM_GA_PRIOR = 1.2   # V4.11: 降低先验值
    FORM_PRIOR_BASE_STRENGTH = 2.0  # V4.11: 降低shrinkage强度（原5.0→2.0），让近期状态数据权重从55%→75%
    FORM_PRIOR_GAMES_OFFSET = 10
    FORM_PRIOR_MAX_STRENGTH = 15.0
    FORM_TREND_MIN_GAMES = 4

    # ============================================================
    # 类别 A++: 联赛归一化
    # ============================================================
    NORM_AVG_HOME_GOALS = 1.5
    NORM_AVG_AWAY_GOALS = 1.2
    NORM_HOME_WIN_RATE = 0.45
    NORM_DRAW_RATE = 0.25
    NORM_AVG_TOTAL_GOALS = 2.7
    NORM_DENOM_MIN = 0.01
    NORM_POSSESSION_FACTOR = 50.0
    NORM_DEFAULT_POSS = 1.0
    NORM_EFFICIENCY_DENOM = 0.1
    NORM_DEFENSIVE_DEFAULT_XGA = 1

    # ============================================================
    # 类别 A+++: 联赛排名
    # ============================================================
    RANK_DEFAULT_POS = 0
    RANK_DEFAULT_PERCENTILE = 0.5
    RANK_DEFAULT_DIFF = 0.0
    RANK_LEAGUE_COUNT_DEFAULT = 200
    RANK_LEAGUE_COUNT_DIVISOR = 19
    RANK_TOTAL_TEAMS_MIN = 8
    RANK_PPG_LHW = 0.45
    RANK_PPG_LD = 0.25
    RANK_SIGMOID_SCALE = 3.0
    RANK_SIGMOID_CENTER = 1.0
    RANK_PERCENTILE_MIN = 0.05
    RANK_PERCENTILE_MAX = 0.95

    # ============================================================
    # 类别 A++++: 欧战经验
    # ============================================================
    UEFA_DEFAULT_MATCHES = 0
    UEFA_DEFAULT_RATE = 0.0

    # ============================================================
    # 类别 F: SM 官方预测
    # ============================================================
    SM_MARKET_THRESHOLD = 0.01
    SM_DEFAULT_PROB = 1.0 / 3.0
    SM_DEFAULT_OU = 0.5
    SM_DEFAULT_BTTS = 0.5
    SM_PROB_DIVISOR = 100

    # ============================================================
    # V4.10: 诱盘检测
    # ============================================================
    INDUCE_DEFAULT_WR = 0.33
    INDUCE_DEFAULT_GOALS = 1.0
    INDUCE_H2H_XG_HOME = 1.35
    INDUCE_H2H_XG_AWAY = 1.15
    INDUCE_H2H_SHOTS_RATIO = 0.5
    INDUCE_H2H_POSSESSION = 50
    INDUCE_H2H_POSS_CENTER = 50
    INDUCE_H2H_DANGER_HOME = 45
    INDUCE_H2H_DANGER_AWAY = 40
    # H2H stats 权重
    INDUCE_H2H_XG_CLAMP = 0.5
    INDUCE_H2H_XG_SCALE = 3.0
    INDUCE_H2H_XG_WEIGHT = 0.40
    INDUCE_H2H_SHOTS_CLAMP = 0.4
    INDUCE_H2H_SHOTS_WEIGHT = 0.25
    INDUCE_H2H_POSS_CLAMP = 0.4
    INDUCE_H2H_POSS_WEIGHT = 0.15
    INDUCE_H2H_DANGER_CLAMP = 0.4
    INDUCE_H2H_DANGER_WEIGHT = 0.20
    INDUCE_H2H_DANGER_SCALE = 100
    # H2H fallback 权重
    INDUCE_H2H_FALLBACK_GOALS_HOME = 1.5
    INDUCE_H2H_FALLBACK_GOALS_AWAY = 1.3
    INDUCE_H2H_FALLBACK_GOALS_CLAMP = 0.5
    INDUCE_H2H_FALLBACK_GOALS_SCALE = 3.0
    INDUCE_H2H_FALLBACK_GOALS_WEIGHT = 0.50
    INDUCE_H2H_FALLBACK_WR_HOME = 0.33
    INDUCE_H2H_FALLBACK_WR_AWAY = 0.33
    INDUCE_H2H_FALLBACK_WR_CLAMP = 0.5
    INDUCE_H2H_FALLBACK_WR_WEIGHT = 0.50
    # 基本面得分权重
    INDUCE_CROSS_LEAGUE_TRUST_MIN = 0.4
    INDUCE_CROSS_LEAGUE_TRUST_RANGE = 0.6
    INDUCE_WR_GAP_LARGE = 0.1
    INDUCE_WR_GAP_MEDIUM = 0.05
    INDUCE_WR_WEIGHT_LARGE = 0.25
    INDUCE_WR_WEIGHT_MEDIUM = 0.12
    INDUCE_HOME_FIELD_SAME_LEAGUE = 0.15
    INDUCE_HOME_FIELD_CROSS_LEAGUE = 0.25
    INDUCE_HOME_FIELD_LEAGUE_THRESH = 0.5
    INDUCE_RANK_DIFF_LARGE = 6
    INDUCE_RANK_DIFF_MEDIUM = 3
    INDUCE_RANK_WEIGHT_LARGE = 0.25
    INDUCE_RANK_WEIGHT_MEDIUM = 0.12
    INDUCE_H2H_WEIGHT_BASE = 0.20
    INDUCE_H2H_WEIGHT_CROSS_BONUS = 0.15
    INDUCE_H2H_SIGNAL_MIN = 0.05
    INDUCE_XG_WEIGHT = 0.15
    INDUCE_XG_THRESHOLD = 0.3
    INDUCE_FUND_THRESH_SAME = 0.10
    INDUCE_FUND_THRESH_CROSS = 0.05
    INDUCE_MARKET_THRESH = 0.02
    INDUCE_DIVERGENCE_SCALE = 3.0

    # ============================================================
    # V4.2: 庄家意图
    # ============================================================
    BI_DEFAULT_WR = 0.33
    BI_DEFAULT_PPG = 1.5
    BI_DEFAULT_ODDS = 2.0
    BI_ODDS_DENOM_MIN = 0.1
    BI_STRONG_WR = 0.40
    BI_STRONG_FORM = 1.6
    BI_STRONG_PPG = 1.8
    BI_WEAK_WR = 0.25
    BI_WEAK_FORM = 1.2
    BI_CHANGE_NORMAL = 0.02
    BI_CHANGE_MEDIUM = 0.06
    BI_BLOCK_INTENT = 0.6
    BI_NEGATIVE_INTENT = 0.8
    BI_MARKET_BULLISH = 0.8
    BI_MARKET_CONSENSUS = 0.3
    BI_LURE_INTENT_MEDIUM = 0.5
    BI_LURE_INTENT_LARGE = 0.7
    BI_WEAK_RISE_INTENT = 0.3
    BI_WEAK_ABANDON_INTENT = 0.5
    BI_BIG_GAP_ODDS_LOW = 1.5
    BI_BIG_GAP_ODDS_HIGH = 5.0
    BI_CONFLICT_REDUCTION = 0.4
    BI_H2H_XG_DIFF = 0.02
    BI_H2H_CONFLICT_REDUCTION = 0.3
    BI_H2H_XG_DEFAULT_HOME = 1.35
    BI_H2H_XG_DEFAULT_AWAY = 1.15
    BI_H2H_XG_TOLERANCE = 0.01
    BI_ODDS_ADVANTAGE_SCALE = 50

    # ============================================================
    # xG 填充
    # ============================================================
    XG_FILL_THRESHOLD = 0.01

    # ============================================================
    # 同联赛标记
    # ============================================================
    SAME_LEAGUE_YES = 1.0
    SAME_LEAGUE_NO = 0.0

    # V4.11: xG 安全裁剪 —— 防止赛季总计混入场均
    MAX_XG_PER_GAME = 5.0
    # V4.11: 市场概率衰减指数 —— 压缩极端市场信号
    MARKET_ATTENUATION_EXPONENT = 0.65

    @staticmethod
    def _safe_xg_per_game(raw_xg, games_played):
        """安全获取场均 xG：如果 raw_xg > MAX_XG_PER_GAME 则为赛季总计，除以场次"""
        if raw_xg is None:
            return 0.0
        val = float(raw_xg)
        if val > FeatureEngineerB.MAX_XG_PER_GAME and games_played and games_played > 0:
            return val / games_played
        if val > FeatureEngineerB.MAX_XG_PER_GAME:
            return 0.0  # 无场次数，无法归一化，归零
        return val

    @staticmethod
    def _attenuate_market_features(features: dict):
        """V4.11: 对市场概率特征做幂变换衰减，降低Poisson模型对赔率信号的过度依赖
        
        原始系数比: market(0.168) / goals(0.026) ≈ 6.5x
        衰减后预期: market(0.168) * p^0.65 / goals(0.026) ≈ 3x
        """
        import math
        exp = FeatureEngineerB.MARKET_ATTENUATION_EXPONENT
        market_keys = [
            "odds_market_home_prob", "odds_market_away_prob", "odds_market_draw_prob",
            "odds_implied_home_change",
        ]
        for key in market_keys:
            if key in features and features[key] is not None:
                val = float(features[key])
                if val > 0.01:
                    # 幂变换压缩高值，保留排序关系
                    features[key] = math.pow(val, exp)
                elif val < -0.01:
                    # 负值（如odds_implied_home_change）保持符号
                    features[key] = -math.pow(abs(val), exp)

    # ────────────────────────────────────────────────────────────
    # 核心入口
    # ────────────────────────────────────────────────────────────

    async def extract_features(self, match_id: int, sm_prediction: dict = None) -> pd.DataFrame:
        """为单场比赛提取特征，返回单行 DataFrame

        Args:
            match_id: 比赛ID
            sm_prediction: SportMonks 官方预测（可选，来自 get_predictions_by_fixture）
        """
        match = await self._get_match(match_id)
        if not match:
            return pd.DataFrame()

        home_stats = await self._get_team_stats(match.home_team_id)
        away_stats = await self._get_team_stats(match.away_team_id)
        h2h = await self._get_h2h(match.home_team_id, match.away_team_id, before_date=match.kickoff_time)
        odds_data = await self._get_odds_structured(match_id)
        home_injuries = await self._get_injury_count(match.home_team_id)
        away_injuries = await self._get_injury_count(match.away_team_id)

        features = {}
        # 类别 A: 球队基础战力
        league_baseline = await self._get_league_baseline(match.league_id)
        features.update(self._extract_team_strength(home_stats, away_stats, league_baseline))
        # 类别 A+: 上下文特征
        features.update(self._extract_contextual(match, home_stats, away_stats))
        # 类别 A+++: 联赛排名特征
        features.update(self._extract_rank_features(home_stats, away_stats, league_baseline, features))
        # 类别 A++++: 欧战经验特征
        home_uefa = await self._get_uefa_experience(match.home_team_id) if match.home_team_id else {}
        away_uefa = await self._get_uefa_experience(match.away_team_id) if match.away_team_id else {}
        features.update(self._extract_uefa_features(home_uefa, away_uefa))
        # 类别 B: 交锋记录
        features.update(self._extract_h2h(h2h, match.home_team_id, match.away_team_id, match.league_id))
        # 类别 C: 赔率信号
        features.update(self._extract_odds(odds_data))
        # 类别 D: 阵容 & 外部
        features["home_injuries"] = home_injuries
        features["away_injuries"] = away_injuries
        current_time = match.kickoff_time.replace(tzinfo=None) if match.kickoff_time else None
        features["home_rest_days"] = await self._get_rest_days(match.home_team_id, current_time)
        features["away_rest_days"] = await self._get_rest_days(match.away_team_id, current_time)
        features["rest_days_diff"] = features["home_rest_days"] - features["away_rest_days"]
        home_same_league = self.SAME_LEAGUE_YES if (match.league_id and home_stats and match.league_id == home_stats.league_id) else self.SAME_LEAGUE_NO
        away_same_league = self.SAME_LEAGUE_YES if (match.league_id and away_stats and match.league_id == away_stats.league_id) else self.SAME_LEAGUE_NO
        features["season_match_same_league"] = min(home_same_league, away_same_league)
        # 类别 E: 近期状态
        features.update(self._extract_recent_form(home_stats, away_stats, features, before_date=match.kickoff_time))
        # 类别 C+: 偏离度特征
        features.update(self._compute_deviation(features))
        # 类别 C++: JC 与市场盘口偏离
        features.update(self._compute_contextual_deviation(features))
        # 类别 F: SM 官方预测
        try:
            features.update(self._extract_sm_predictions(sm_prediction, features))
        except Exception:
            pass
        # xG 数据用 H2H 交锋记录填充
        self._fill_xg_from_h2h(features)
        # 类别 A++: 联赛归一化特征
        features.update(self._normalize_by_league(features, league_baseline, home_stats, away_stats))
        # V4.10: 基本面-市场背离检测
        self._detect_inducement(features)
        # V4.2: 庄家意图特征
        self._add_bookmaker_intent(features)

        # V4.11: 市场概率特征衰减 —— 降低市场信号在Poisson模型中的主导
        self._attenuate_market_features(features)

        return pd.DataFrame([features])

    # ────────────────────────────────────────────────────────────
    # xG 填充
    # ────────────────────────────────────────────────────────────

    def _fill_xg_from_h2h(self, features: dict):
        """当全局 xG 缺失时，用 H2H 交锋记录 xG 填充"""
        h2h_home_xg = features.get("h2h_avg_home_xg", self.H2H_DEFAULT_XG_HOME - self.H2H_DEFAULT_XG_HOME)  # 0
        h2h_away_xg = features.get("h2h_avg_away_xg", self.H2H_DEFAULT_XG_HOME - self.H2H_DEFAULT_XG_HOME)  # 0

        if abs(features.get("home_xG", self.DEFAULT_XG)) < self.XG_FILL_THRESHOLD and h2h_home_xg > self.DEFAULT_XG:
            features["home_xG"] = h2h_home_xg
            features["xG_diff"] = features["home_xG"] - features.get("away_xGA", self.DEFAULT_XG)

        if abs(features.get("away_xGA", self.DEFAULT_XG)) < self.XG_FILL_THRESHOLD and h2h_home_xg > self.DEFAULT_XG:
            features["away_xGA"] = h2h_home_xg

        if abs(features.get("away_xG", self.DEFAULT_XG)) < self.XG_FILL_THRESHOLD and h2h_away_xg > self.DEFAULT_XG:
            features["away_xG"] = h2h_away_xg
            features["xG_diff"] = features.get("home_xG", self.DEFAULT_XG) - features["away_xG"]

        if abs(features.get("home_xGA", self.DEFAULT_XG)) < self.XG_FILL_THRESHOLD and h2h_away_xg > self.DEFAULT_XG:
            features["home_xGA"] = h2h_away_xg

    # ────────────────────────────────────────────────────────────
    # V4.10: 诱盘检测
    # ────────────────────────────────────────────────────────────

    def _detect_inducement(self, features: dict):
        """基本面-市场背离检测（诱盘场景识别）

        核心逻辑：当市场赔率走势与基本面数据方向矛盾时，市场可能在诱导资金。
        """
        home_wr = features.get("home_win_rate", self.INDUCE_DEFAULT_WR)
        away_wr = features.get("away_win_rate", self.INDUCE_DEFAULT_WR)
        home_rank = features.get("home_league_rank", self.RANK_DEFAULT_POS)
        away_rank = features.get("away_league_rank", self.RANK_DEFAULT_POS)
        league_rank_gap = features.get("league_rank_gap", self.RANK_DEFAULT_DIFF)
        home_xg = features.get("home_goals_avg", self.INDUCE_DEFAULT_GOALS) - features.get("away_goals_against_avg", self.INDUCE_DEFAULT_GOALS)

        has_h2h = features.get("has_h2h", self.H2H_NO_DATA) or self.H2H_NO_DATA
        h2h_stats_ok = (features.get("h2h_stats_available", self.H2H_NO_DATA) or self.H2H_NO_DATA) > self.INDUCE_H2H_SIGNAL_MIN
        h2h_signal = 0.0
        if has_h2h > self.H2H_NO_DATA:
            if h2h_stats_ok:
                h2h_xg = features.get("h2h_avg_home_xg", self.INDUCE_H2H_XG_HOME) - features.get("h2h_avg_away_xg", self.INDUCE_H2H_XG_AWAY)
                h2h_shots = features.get("h2h_avg_shots_ratio", self.INDUCE_H2H_SHOTS_RATIO) - self.INDUCE_H2H_SHOTS_RATIO
                h2h_poss = (features.get("h2h_avg_home_possession", self.INDUCE_H2H_POSSESSION) - self.INDUCE_H2H_POSS_CENTER) / self.INDUCE_H2H_POSS_CENTER
                h2h_danger = features.get("h2h_avg_home_dangerous", self.INDUCE_H2H_DANGER_HOME) - features.get("h2h_avg_away_dangerous", self.INDUCE_H2H_DANGER_AWAY)
                h2h_signal = (
                    max(-self.INDUCE_H2H_XG_CLAMP, min(self.INDUCE_H2H_XG_CLAMP, h2h_xg / self.INDUCE_H2H_XG_SCALE)) * self.INDUCE_H2H_XG_WEIGHT +
                    max(-self.INDUCE_H2H_SHOTS_CLAMP, min(self.INDUCE_H2H_SHOTS_CLAMP, h2h_shots)) * self.INDUCE_H2H_SHOTS_WEIGHT +
                    max(-self.INDUCE_H2H_POSS_CLAMP, min(self.INDUCE_H2H_POSS_CLAMP, h2h_poss)) * self.INDUCE_H2H_POSS_WEIGHT +
                    max(-self.INDUCE_H2H_DANGER_CLAMP, min(self.INDUCE_H2H_DANGER_CLAMP, h2h_danger / self.INDUCE_H2H_DANGER_SCALE)) * self.INDUCE_H2H_DANGER_WEIGHT
                )
            else:
                h2h_goals = features.get("h2h_avg_home_goals", self.INDUCE_H2H_FALLBACK_GOALS_HOME) - features.get("h2h_avg_away_goals", self.INDUCE_H2H_FALLBACK_GOALS_AWAY)
                h2h_wr = features.get("h2h_home_win_rate", self.INDUCE_H2H_FALLBACK_WR_HOME) - features.get("h2h_away_win_rate", self.INDUCE_H2H_FALLBACK_WR_AWAY)
                h2h_signal = (
                    max(-self.INDUCE_H2H_FALLBACK_GOALS_CLAMP, min(self.INDUCE_H2H_FALLBACK_GOALS_CLAMP, h2h_goals / self.INDUCE_H2H_FALLBACK_GOALS_SCALE)) * self.INDUCE_H2H_FALLBACK_GOALS_WEIGHT +
                    max(-self.INDUCE_H2H_FALLBACK_WR_CLAMP, min(self.INDUCE_H2H_FALLBACK_WR_CLAMP, h2h_wr)) * self.INDUCE_H2H_FALLBACK_WR_WEIGHT
                )

        fundamental_score = 0.0
        same_league = features.get("season_match_same_league", self.SAME_LEAGUE_NO) or self.SAME_LEAGUE_NO
        data_trust = self.INDUCE_CROSS_LEAGUE_TRUST_MIN + self.INDUCE_CROSS_LEAGUE_TRUST_RANGE * same_league

        wr_gap = home_wr - away_wr
        if abs(wr_gap) > self.INDUCE_WR_GAP_LARGE:
            fundamental_score += data_trust * self.INDUCE_WR_WEIGHT_LARGE * (self.POINTS_PER_DRAW if wr_gap > self.DEFAULT_XG else -self.POINTS_PER_DRAW)
        elif abs(wr_gap) > self.INDUCE_WR_GAP_MEDIUM:
            fundamental_score += data_trust * self.INDUCE_WR_WEIGHT_MEDIUM * (self.POINTS_PER_DRAW if wr_gap > self.DEFAULT_XG else -self.POINTS_PER_DRAW)

        home_field_weight = self.INDUCE_HOME_FIELD_SAME_LEAGUE if same_league >= self.INDUCE_HOME_FIELD_LEAGUE_THRESH else self.INDUCE_HOME_FIELD_CROSS_LEAGUE
        fundamental_score += home_field_weight

        if home_rank > self.RANK_DEFAULT_POS and away_rank > self.RANK_DEFAULT_POS:
            rank_diff = away_rank - home_rank
            if abs(rank_diff) > self.INDUCE_RANK_DIFF_LARGE:
                fundamental_score += data_trust * self.INDUCE_RANK_WEIGHT_LARGE * (self.POINTS_PER_DRAW if rank_diff > self.RANK_DEFAULT_POS else -self.POINTS_PER_DRAW)
            elif abs(rank_diff) > self.INDUCE_RANK_DIFF_MEDIUM:
                fundamental_score += data_trust * self.INDUCE_RANK_WEIGHT_MEDIUM * (self.POINTS_PER_DRAW if rank_diff > self.RANK_DEFAULT_POS else -self.POINTS_PER_DRAW)
        else:
            if abs(league_rank_gap) > self.INDUCE_RANK_DIFF_LARGE:
                fundamental_score += data_trust * self.INDUCE_RANK_WEIGHT_LARGE * (self.POINTS_PER_DRAW if league_rank_gap > self.RANK_DEFAULT_POS else -self.POINTS_PER_DRAW)

        h2h_weight = self.INDUCE_H2H_WEIGHT_BASE + self.INDUCE_H2H_WEIGHT_CROSS_BONUS * (self.POINTS_PER_DRAW - same_league)
        if abs(h2h_signal) > self.INDUCE_H2H_SIGNAL_MIN:
            fundamental_score += h2h_weight * (self.POINTS_PER_DRAW if h2h_signal > self.DEFAULT_XG else -self.POINTS_PER_DRAW)

        if abs(home_xg) > self.INDUCE_XG_THRESHOLD:
            fundamental_score += data_trust * self.INDUCE_XG_WEIGHT * (self.POINTS_PER_DRAW if home_xg > self.DEFAULT_XG else -self.POINTS_PER_DRAW)

        odds_mv_home = features.get("odds_movement_home", self.ODDS_DEFAULT_VAL) or self.ODDS_DEFAULT_VAL
        odds_mv_away = features.get("odds_movement_away", self.ODDS_DEFAULT_VAL) or self.ODDS_DEFAULT_VAL
        market_score = odds_mv_home - odds_mv_away

        fund_threshold = self.INDUCE_FUND_THRESH_CROSS if same_league < self.INDUCE_HOME_FIELD_LEAGUE_THRESH else self.INDUCE_FUND_THRESH_SAME
        divergence = 0.0
        if abs(fundamental_score) > fund_threshold and abs(market_score) > self.INDUCE_MARKET_THRESH:
            fund_sign = self.POINTS_PER_DRAW if fundamental_score > self.DEFAULT_XG else -self.POINTS_PER_DRAW
            market_sign = self.POINTS_PER_DRAW if market_score > self.DEFAULT_XG else -self.POINTS_PER_DRAW
            if fund_sign != market_sign:
                raw_divergence = abs(fundamental_score) * abs(market_score)
                divergence = fund_sign * raw_divergence * self.INDUCE_DIVERGENCE_SCALE

        features["fundamental_vs_market_divergence"] = round(divergence, self.FORM_TREND_MIN_GAMES)
        features["fundamental_score"] = round(fundamental_score, self.FORM_TREND_MIN_GAMES)
        features["market_direction_score"] = round(market_score, self.FORM_TREND_MIN_GAMES)

    # ────────────────────────────────────────────────────────────
    # V4.2: 庄家意图
    # ────────────────────────────────────────────────────────────

    def _add_bookmaker_intent(self, features: dict):
        """庄家意图特征——赔率变动幅度 + 基本面交叉"""
        home_win_rate = features.get("home_win_rate", self.BI_DEFAULT_WR)
        home_form = features.get("home_form_pts_6", self.FORM_DEFAULT)
        home_ppg = features.get("home_points_per_game", self.BI_DEFAULT_PPG)
        away_win_rate = features.get("away_win_rate", self.BI_DEFAULT_WR)
        away_form = features.get("away_form_pts_6", self.FORM_DEFAULT)
        away_ppg = features.get("away_points_per_game", self.BI_DEFAULT_PPG)

        odds_mv_home = features.get("odds_movement_home", self.FORM_DEFAULT)
        odds_mv_away = features.get("odds_movement_away", self.FORM_DEFAULT)
        odds_init_home = features.get("odds_home_initial", self.BI_DEFAULT_ODDS)
        odds_init_away = features.get("odds_away_initial", self.BI_DEFAULT_ODDS)

        home_change_pct = -odds_mv_home / max(odds_init_home, self.BI_ODDS_DENOM_MIN) if odds_init_home else self.FORM_DEFAULT
        away_change_pct = -odds_mv_away / max(odds_init_away, self.BI_ODDS_DENOM_MIN) if odds_init_away else self.FORM_DEFAULT

        home_strong = home_win_rate > self.BI_STRONG_WR or home_form > self.BI_STRONG_FORM or home_ppg > self.BI_STRONG_PPG
        away_strong = away_win_rate > self.BI_STRONG_WR or away_form > self.BI_STRONG_FORM or away_ppg > self.BI_STRONG_PPG
        home_weak = home_win_rate < self.BI_WEAK_WR and home_form < self.BI_WEAK_FORM
        away_weak = away_win_rate < self.BI_WEAK_WR and away_form < self.BI_WEAK_FORM

        def _classify_change(pct):
            ap = abs(pct)
            if ap < self.BI_CHANGE_NORMAL:
                return 0
            if ap < self.BI_CHANGE_MEDIUM:
                return self.POINTS_PER_DRAW if pct > self.DEFAULT_XG else (-self.POINTS_PER_DRAW if pct < self.DEFAULT_XG else 0)
            return self.H2H_POINTS_DRAW if pct > self.DEFAULT_XG else (-self.H2H_POINTS_DRAW if pct < self.DEFAULT_XG else 0)

        h_change = _classify_change(home_change_pct)
        a_change = _classify_change(away_change_pct)

        features["bookmaker_block_home"] = self.SAME_LEAGUE_YES if (home_strong and h_change == self.POINTS_PER_DRAW) else self.SAME_LEAGUE_NO
        features["bookmaker_block_away"] = self.SAME_LEAGUE_YES if (away_strong and a_change == self.POINTS_PER_DRAW) else self.SAME_LEAGUE_NO
        features["bookmaker_negative_home"] = self.SAME_LEAGUE_YES if (home_strong and h_change == self.H2H_POINTS_DRAW) else self.SAME_LEAGUE_NO
        features["bookmaker_negative_away"] = self.SAME_LEAGUE_YES if (away_strong and a_change == self.H2H_POINTS_DRAW) else self.SAME_LEAGUE_NO
        features["bookmaker_lure_home"] = self.SAME_LEAGUE_YES if (home_weak and h_change in (-self.POINTS_PER_DRAW, -self.H2H_POINTS_DRAW)) else self.SAME_LEAGUE_NO
        features["bookmaker_lure_away"] = self.SAME_LEAGUE_YES if (away_weak and a_change in (-self.POINTS_PER_DRAW, -self.H2H_POINTS_DRAW)) else self.SAME_LEAGUE_NO

        big_gap_home = home_strong and away_weak
        big_gap_away = away_strong and home_weak
        odds_gap_home = odds_init_home < self.BI_BIG_GAP_ODDS_LOW and odds_init_away > self.BI_BIG_GAP_ODDS_HIGH
        odds_gap_away = odds_init_away < self.BI_BIG_GAP_ODDS_LOW and odds_init_home > self.BI_BIG_GAP_ODDS_HIGH
        big_gap_home = big_gap_home or odds_gap_home
        big_gap_away = big_gap_away or odds_gap_away

        intent = 0.0
        if home_strong and h_change == self.POINTS_PER_DRAW:
            intent += self.BI_BLOCK_INTENT
        if away_strong and a_change == self.POINTS_PER_DRAW:
            intent -= self.BI_BLOCK_INTENT
        if home_strong and h_change == self.H2H_POINTS_DRAW:
            intent -= self.BI_NEGATIVE_INTENT
        if away_strong and a_change == self.H2H_POINTS_DRAW:
            intent += self.BI_NEGATIVE_INTENT
        if home_strong and h_change == -self.H2H_POINTS_DRAW:
            intent += self.BI_MARKET_BULLISH
        if away_strong and a_change == -self.H2H_POINTS_DRAW:
            intent -= self.BI_MARKET_BULLISH
        if home_strong and h_change == -self.POINTS_PER_DRAW:
            intent += self.BI_MARKET_CONSENSUS
        if away_strong and a_change == -self.POINTS_PER_DRAW:
            intent -= self.BI_MARKET_CONSENSUS
        if home_weak and h_change == -self.POINTS_PER_DRAW:
            intent -= self.BI_LURE_INTENT_MEDIUM
        if away_weak and a_change == -self.POINTS_PER_DRAW:
            intent += self.BI_LURE_INTENT_MEDIUM
        if home_weak and h_change == -self.H2H_POINTS_DRAW:
            intent -= self.BI_LURE_INTENT_LARGE
        if away_weak and a_change == -self.H2H_POINTS_DRAW:
            intent += self.BI_LURE_INTENT_LARGE
        if home_weak and h_change == self.POINTS_PER_DRAW:
            intent += self.BI_WEAK_RISE_INTENT
        if away_weak and a_change == self.POINTS_PER_DRAW:
            intent -= self.BI_WEAK_RISE_INTENT
        if home_weak and h_change == self.H2H_POINTS_DRAW:
            intent += self.BI_WEAK_ABANDON_INTENT
        if away_weak and a_change == self.H2H_POINTS_DRAW:
            intent -= self.BI_WEAK_ABANDON_INTENT
        if big_gap_home and a_change == -self.H2H_POINTS_DRAW:
            intent += self.BI_WEAK_ABANDON_INTENT
        if big_gap_away and h_change == -self.H2H_POINTS_DRAW:
            intent -= self.BI_WEAK_ABANDON_INTENT
        if big_gap_home and a_change == -self.POINTS_PER_DRAW:
            intent += self.BI_MARKET_CONSENSUS
        if big_gap_away and h_change == -self.POINTS_PER_DRAW:
            intent -= self.BI_MARKET_CONSENSUS
        features["bookmaker_intent"] = max(-self.SAME_LEAGUE_YES, min(self.SAME_LEAGUE_YES, intent))

        stats_favor_home = (home_strong and not away_strong) or (away_weak and not home_weak)
        stats_favor_away = (away_strong and not home_strong) or (home_weak and not away_weak)
        odds_favor_home = odds_init_home < odds_init_away
        if odds_favor_home and stats_favor_away:
            features["bookmaker_intent"] *= self.BI_CONFLICT_REDUCTION
        elif (not odds_favor_home) and stats_favor_home:
            features["bookmaker_intent"] *= self.BI_CONFLICT_REDUCTION

        h2h_home_xg = features.get("h2h_avg_home_xg", self.FORM_DEFAULT) or self.FORM_DEFAULT
        h2h_away_xg = features.get("h2h_avg_away_xg", self.FORM_DEFAULT) or self.FORM_DEFAULT
        has_h2h = features.get("has_h2h", self.FORM_DEFAULT) or features.get("h2h_match_count", self.FORM_DEFAULT)
        h2h_favor_home = False
        h2h_favor_away = False
        odds_favor_home_move = home_change_pct < -self.BI_CHANGE_NORMAL
        odds_favor_away_move = away_change_pct < -self.BI_CHANGE_NORMAL
        if has_h2h and (h2h_home_xg > self.FORM_DEFAULT or h2h_away_xg > self.FORM_DEFAULT):
            h2h_favor_home = h2h_home_xg > h2h_away_xg + self.BI_H2H_XG_DIFF
            h2h_favor_away = h2h_away_xg > h2h_home_xg + self.BI_H2H_XG_DIFF

            if h2h_favor_home and odds_favor_away_move:
                if features["bookmaker_intent"] < self.FORM_DEFAULT:
                    features["bookmaker_intent"] *= self.BI_H2H_CONFLICT_REDUCTION
            elif h2h_favor_away and odds_favor_home_move:
                if features["bookmaker_intent"] > self.FORM_DEFAULT:
                    features["bookmaker_intent"] *= self.BI_H2H_CONFLICT_REDUCTION

        h2h_dir = self.RANK_DEFAULT_POS
        if h2h_favor_home:
            h2h_dir = self.POINTS_PER_DRAW
        elif h2h_favor_away:
            h2h_dir = -self.POINTS_PER_DRAW

        odds_mv_dir = self.RANK_DEFAULT_POS
        if odds_favor_home_move:
            odds_mv_dir = self.POINTS_PER_DRAW
        elif odds_favor_away_move:
            odds_mv_dir = -self.POINTS_PER_DRAW

        h2h_xg_real = h2h_home_xg != self.BI_H2H_XG_DEFAULT_HOME or h2h_away_xg != self.BI_H2H_XG_DEFAULT_AWAY
        if not h2h_xg_real:
            raw_xg_diff = abs(h2h_home_xg - h2h_away_xg)
            h2h_xg_real = raw_xg_diff > self.BI_H2H_XG_TOLERANCE and not (abs(h2h_home_xg - self.BI_H2H_XG_DEFAULT_HOME) < self.BI_H2H_XG_TOLERANCE and abs(h2h_away_xg - self.BI_H2H_XG_DEFAULT_AWAY) < self.BI_H2H_XG_TOLERANCE)

        if h2h_dir != self.RANK_DEFAULT_POS and odds_mv_dir != self.RANK_DEFAULT_POS and h2h_xg_real:
            features["h2h_odds_alignment"] = self.SAME_LEAGUE_YES if h2h_dir == odds_mv_dir else -self.SAME_LEAGUE_YES
        else:
            features["h2h_odds_alignment"] = self.SAME_LEAGUE_NO

        fundamental_advantage = home_ppg - away_ppg
        odds_advantage = -home_change_pct + away_change_pct
        features["fundamental_odds_divergence"] = abs(fundamental_advantage + odds_advantage * self.BI_ODDS_ADVANTAGE_SCALE)

    # ────────────────────────────────────────────────────────────
    # 类别 A: 球队基础战力
    # ────────────────────────────────────────────────────────────

    def _extract_team_strength(self, home, away, baseline: dict = None) -> dict:
        """类别 A: 球队基础战力特征"""
        if baseline is None:
            baseline = {}
        feats = {}

        # 主队
        if home and home.played and home.played > 0:
            p = home.played
            feats["home_win_rate"] = home.wins / p
            feats["home_draw_rate"] = home.draws / p
            feats["home_goals_avg"] = home.goals_for / p
            feats["home_goals_against_avg"] = home.goals_against / p
            feats["home_home_win_rate"] = home.home_wins / max(home.home_wins + home.home_draws + home.home_losses, self.DENOM_MIN_1)
            feats["home_clean_sheet_rate"] = home.clean_sheets / max(p, self.DENOM_MIN_1)
            feats["home_xG"] = self._safe_xg_per_game(home.xG, p)
            feats["home_xGA"] = self._safe_xg_per_game(home.xGA, p)
            home_pts = home.wins * self.POINTS_PER_WIN + home.draws
            feats["home_points_per_game"] = home_pts / p
        else:
            feats["home_win_rate"] = baseline.get("league_home_win_rate", self.TEAM_STRENGTH_DEFAULT_HWR)
            feats["home_draw_rate"] = baseline.get("league_draw_rate", self.DEFAULT_DRAW_RATE)
            feats["home_goals_avg"] = baseline.get("league_avg_home_goals", self.DEFAULT_HOME_GOALS)
            feats["home_goals_against_avg"] = baseline.get("league_avg_away_goals", self.DEFAULT_AWAY_GOALS)
            feats["home_home_win_rate"] = baseline.get("league_home_win_rate", self.TEAM_STRENGTH_DEFAULT_HWR)
            feats["home_clean_sheet_rate"] = self.DEFAULT_CLEAN_SHEET_RATE
            feats["home_xG"] = self.DEFAULT_XG
            feats["home_xGA"] = self.DEFAULT_XG
            feats["home_points_per_game"] = baseline.get("league_home_win_rate", self.TEAM_STRENGTH_DEFAULT_HWR) * self.POINTS_PER_WIN + baseline.get("league_draw_rate", self.DEFAULT_DRAW_RATE)

        # 客队
        if away and away.played and away.played > 0:
            p = away.played
            feats["away_win_rate"] = away.wins / p
            feats["away_goals_avg"] = away.goals_for / p
            feats["away_goals_against_avg"] = away.goals_against / p
            feats["away_away_win_rate"] = away.away_wins / max(away.away_wins + away.away_draws + away.away_losses, self.DENOM_MIN_1)
            feats["away_xG"] = self._safe_xg_per_game(away.xG, p)
            feats["away_xGA"] = self._safe_xg_per_game(away.xGA, p)
            away_pts = away.wins * self.POINTS_PER_WIN + away.draws
            feats["away_points_per_game"] = away_pts / p
        else:
            feats["away_win_rate"] = self.SAME_LEAGUE_YES - baseline.get("league_home_win_rate", self.TEAM_STRENGTH_DEFAULT_HWR) - baseline.get("league_draw_rate", self.DEFAULT_DRAW_RATE)
            feats["away_goals_avg"] = baseline.get("league_avg_away_goals", self.DEFAULT_AWAY_GOALS)
            feats["away_goals_against_avg"] = baseline.get("league_avg_home_goals", self.DEFAULT_HOME_GOALS)
            feats["away_away_win_rate"] = self.SAME_LEAGUE_YES - baseline.get("league_home_win_rate", self.TEAM_STRENGTH_DEFAULT_HWR) - baseline.get("league_draw_rate", self.DEFAULT_DRAW_RATE)
            feats["away_xG"] = self.DEFAULT_XG
            feats["away_xGA"] = self.DEFAULT_XG
            feats["away_points_per_game"] = (self.SAME_LEAGUE_YES - baseline.get("league_home_win_rate", self.TEAM_STRENGTH_DEFAULT_HWR) - baseline.get("league_draw_rate", self.DEFAULT_DRAW_RATE)) * self.POINTS_PER_WIN + baseline.get("league_draw_rate", self.DEFAULT_DRAW_RATE)

        # 差值
        feats["win_rate_diff"] = feats.get("home_win_rate", self.DEFAULT_XG) - feats.get("away_win_rate", self.DEFAULT_XG)
        feats["goals_avg_diff"] = feats.get("home_goals_avg", self.DEFAULT_XG) - feats.get("away_goals_against_avg", self.DEFAULT_XG)
        feats["xG_diff"] = feats.get("home_xG", self.DEFAULT_XG) - feats.get("away_xGA", self.DEFAULT_XG)
        feats["points_per_game_diff"] = feats.get("home_points_per_game", self.DEFAULT_XG) - feats.get("away_points_per_game", self.DEFAULT_XG)

        hwr = max(feats.get("home_win_rate", self.DEFAULT_XG), self.DENOM_MIN)
        awr = max(feats.get("away_win_rate", self.DEFAULT_XG), self.DENOM_MIN)
        feats["match_intensity"] = (hwr * awr) ** self.TEAM_STRENGTH_MATCH_INTENSITY_EXP
        total_strength = hwr + awr
        feats["strength_asymmetry"] = abs(hwr - awr) / total_strength

        return feats

    # ────────────────────────────────────────────────────────────
    # 类别 A+: 上下文特征
    # ────────────────────────────────────────────────────────────

    def _extract_contextual(self, match, home_stats, away_stats) -> dict:
        """类别 A+: 上下文特征"""
        feats = {}

        feats["league_id"] = float(match.league_id) if match.league_id else self.CONTEXT_DEFAULT_LEAGUE_ID

        if match.kickoff_time:
            month = match.kickoff_time.month
            if month in self.CONTEXT_MONTHS_EARLY:
                feats["season_stage"] = self.CONTEXT_SEASON_STAGE_EARLY
            elif month in self.CONTEXT_MONTHS_MID:
                feats["season_stage"] = self.CONTEXT_SEASON_STAGE_MID
            else:
                feats["season_stage"] = self.CONTEXT_SEASON_STAGE_LATE
            weekday = match.kickoff_time.weekday()
            feats["is_weekend"] = self.SAME_LEAGUE_YES if weekday >= self.CONTEXT_WEEKDAY_THRESHOLD else self.SAME_LEAGUE_NO
        else:
            feats["season_stage"] = self.CONTEXT_DEFAULT_SEASON_STAGE
            feats["is_weekend"] = self.SAME_LEAGUE_NO

        feats["home_games_played"] = float(home_stats.played) if home_stats else self.CONTEXT_DEFAULT_GAMES_PLAYED
        feats["away_games_played"] = float(away_stats.played) if away_stats else self.CONTEXT_DEFAULT_GAMES_PLAYED

        season_total = max(feats["home_games_played"] + feats.get("home_win_rate", self.CONTEXT_DEFAULT_GAMES_PLAYED) * self.CONTEXT_DEFAULT_GAMES_PLAYED + self.CONTEXT_SEASON_TOTAL_DEFAULT, self.CONTEXT_SEASON_TOTAL_MIN)
        feats["season_progress_ratio"] = min(
            ((feats["home_games_played"] + feats["away_games_played"]) / self.H2H_POINTS_DRAW) / season_total, self.CONTEXT_PROGRESS_RATIO_MAX
        )

        if home_stats and away_stats:
            home_pts = home_stats.wins * self.POINTS_PER_WIN + home_stats.draws
            away_pts = away_stats.wins * self.POINTS_PER_WIN + away_stats.draws
            feats["points_diff"] = float(home_pts - away_pts)
        else:
            feats["points_diff"] = self.CONTEXT_DEFAULT_POINTS_DIFF

        feats["handicap_line"] = float(match.handicap_line) if match.handicap_line else self.CONTEXT_DEFAULT_POINTS_DIFF

        ss = feats.get("season_stage", self.CONTEXT_DEFAULT_SEASON_STAGE)
        if ss <= self.CONTEXT_STAGE_THRESHOLD_EARLY:
            stage_factor = self.CONTEXT_STAGE_FACTOR_EARLY
        elif ss <= self.CONTEXT_STAGE_THRESHOLD_MID:
            stage_factor = self.CONTEXT_STAGE_FACTOR_MID
        else:
            stage_factor = self.CONTEXT_STAGE_FACTOR_LATE
        feats["home_motivation"] = self._calc_motivation(home_stats, stage_factor)
        feats["away_motivation"] = self._calc_motivation(away_stats, stage_factor)
        feats["motivation_diff"] = feats["home_motivation"] - feats["away_motivation"]

        return feats

    # ────────────────────────────────────────────────────────────
    # 动机压力
    # ────────────────────────────────────────────────────────────

    def _calc_motivation(self, stats, stage_factor: float, total_games: int = None) -> float:
        """计算球队动机压力（0~1）"""
        if total_games is None:
            total_games = self.MOTIVATION_TOTAL_GAMES
        if not stats or not stats.played or stats.played == self.RANK_DEFAULT_POS:
            return self.MOTIVATION_DEFAULT * stage_factor

        ppg = (stats.wins * self.POINTS_PER_WIN + stats.draws) / stats.played
        progress = min(stats.played / total_games, self.SAME_LEAGUE_YES)

        if ppg >= self.MOTIVATION_CHAMPION_PPG:
            raw = self.MOTIVATION_CHAMPION_BASE + self.MOTIVATION_CHAMPION_RANGE * progress
        elif ppg >= self.MOTIVATION_EUROPE_PPG:
            raw = self.MOTIVATION_EUROPE_BASE + self.MOTIVATION_EUROPE_RANGE * progress
        elif ppg >= self.MOTIVATION_MID_PPG:
            raw = max(self.MOTIVATION_MID_MIN, self.MOTIVATION_MID_BASE - self.MOTIVATION_MID_DECAY * progress)
        else:
            raw = self.MOTIVATION_RELEGATION_BASE + self.MOTIVATION_RELEGATION_RANGE * progress

        return round(raw * stage_factor, self.FORM_TREND_MIN_GAMES)

    # ────────────────────────────────────────────────────────────
    # 类别 B: H2H 交锋
    # ────────────────────────────────────────────────────────────

    def _extract_h2h(self, h2h_list: list, home_team_id: int, away_team_id: int, league_id: int = None) -> dict:
        """类别 B: 交锋记录特征"""
        # 韩K 射门→进球转化率低，衰减 shots 特征值
        shots_atten = self.H2H_SHOTS_LEAGUE_ATTENUATION.get(league_id, 1.0) if league_id else 1.0
        has_h2h = len(h2h_list) >= self.DENOM_MIN_1
        feats = {
            "has_h2h": self.H2H_HAS_DATA if has_h2h else self.H2H_NO_DATA,
            "h2h_match_count": len(h2h_list),
            "h2h_avg_home_xg": self.H2H_DEFAULT_XG_HOME,
            "h2h_avg_away_xg": self.H2H_DEFAULT_XG_AWAY,
            "h2h_avg_xg_diff": self.H2H_DEFAULT_XG_DIFF,
            "h2h_avg_home_shots": self.H2H_DEFAULT_SHOTS_HOME,
            "h2h_avg_away_shots": self.H2H_DEFAULT_SHOTS_AWAY,
            "h2h_avg_shots_ratio": self.H2H_DEFAULT_SHOTS_RATIO,
            "h2h_avg_home_possession": self.H2H_DEFAULT_POSSESSION,
            "h2h_avg_home_dangerous": self.H2H_DEFAULT_DANGER_HOME,
            "h2h_avg_away_dangerous": self.H2H_DEFAULT_DANGER_AWAY,
            "h2h_avg_home_goals": self.H2H_DEFAULT_GOALS_HOME,
            "h2h_avg_away_goals": self.H2H_DEFAULT_GOALS_AWAY,
            "h2h_home_win_rate": self.H2H_DEFAULT_WIN_RATE,
            "h2h_away_win_rate": self.H2H_DEFAULT_WIN_RATE,
            "h2h_draw_rate": self.H2H_DEFAULT_DRAW_RATE,
            "h2h_stats_available": self.H2H_DEFAULT_STATS_AVAILABLE,
        }

        home_xgs, away_xgs = [], []
        home_shots, away_shots = [], []
        home_poss, home_dang, away_dang = [], [], []
        home_goals, away_goals = [], []
        home_wins, away_wins, draws = 0, 0, 0

        for h in h2h_list:
            if h.home_score is None or h.away_score is None:
                continue

            if h.home_team_id == home_team_id:
                h_g, a_g = h.home_score, h.away_score
            else:
                h_g, a_g = h.away_score, h.home_score

            home_goals.append(h_g)
            away_goals.append(a_g)
            if h_g > a_g:
                home_wins += self.DENOM_MIN_1
            elif h_g < a_g:
                away_wins += self.DENOM_MIN_1
            else:
                draws += self.DENOM_MIN_1

            hs = h.home_stats or {}
            aws = h.away_stats or {}
            if not hs and not aws:
                continue

            if h.home_team_id == home_team_id:
                h_stats, a_stats = hs, aws
            else:
                h_stats, a_stats = aws, hs

            hx = h_stats.get("xG")
            ax = a_stats.get("xG")
            if hx is not None and ax is not None:
                home_xgs.append(float(hx))
                away_xgs.append(float(ax))

            hsht = h_stats.get("shots")
            asht = a_stats.get("shots")
            if hsht is not None and asht is not None:
                home_shots.append(float(hsht))
                away_shots.append(float(asht))

            hpos = h_stats.get("possession")
            if hpos is not None:
                home_poss.append(float(hpos))

            hd = h_stats.get("dangerous")
            ad = a_stats.get("dangerous")
            if hd is not None:
                home_dang.append(float(hd))
            if ad is not None:
                away_dang.append(float(ad))

        total_matches = home_wins + away_wins + draws
        if total_matches > self.RANK_DEFAULT_POS:
            feats["h2h_home_win_rate"] = round(home_wins / total_matches, self.FORM_TREND_MIN_GAMES)
            feats["h2h_away_win_rate"] = round(away_wins / total_matches, self.FORM_TREND_MIN_GAMES)
            feats["h2h_draw_rate"] = round(draws / total_matches, self.FORM_TREND_MIN_GAMES)

        if home_goals:
            feats["h2h_avg_home_goals"] = round(sum(home_goals) / len(home_goals), self.H2H_POINTS_DRAW)
            feats["h2h_avg_away_goals"] = round(sum(away_goals) / len(away_goals), self.H2H_POINTS_DRAW)

        def _smooth_mean(vals, prior_mean, prior_strength=None):
            if prior_strength is None:
                prior_strength = self.H2H_BAYES_PRIOR_STRENGTH
            if not vals:
                return prior_mean
            ps_val = prior_strength if isinstance(prior_strength, (int, float)) else self.H2H_BAYES_PRIOR_STRENGTH
            return (sum(vals) + prior_mean * ps_val) / (len(vals) + ps_val)

        if home_xgs:
            feats["h2h_avg_home_xg"] = _smooth_mean(home_xgs, self.H2H_DEFAULT_XG_HOME, prior_strength=self.H2H_BAYES_XG_PRIOR_STRENGTH)
            feats["h2h_avg_away_xg"] = _smooth_mean(away_xgs, self.H2H_DEFAULT_XG_AWAY, prior_strength=self.H2H_BAYES_XG_PRIOR_STRENGTH)
            feats["h2h_avg_xg_diff"] = feats["h2h_avg_home_xg"] - feats["h2h_avg_away_xg"]
            feats["h2h_stats_available"] = self.H2H_HAS_DATA

        if home_shots:
            feats["h2h_avg_home_shots"] = _smooth_mean(home_shots, self.H2H_DEFAULT_SHOTS_HOME) * shots_atten
            feats["h2h_avg_away_shots"] = _smooth_mean(away_shots, self.H2H_DEFAULT_SHOTS_AWAY) * shots_atten
            total = feats["h2h_avg_home_shots"] + feats["h2h_avg_away_shots"]
            feats["h2h_avg_shots_ratio"] = feats["h2h_avg_home_shots"] / max(total, self.DENOM_MIN_0_1)

        if home_poss:
            raw_poss = _smooth_mean(home_poss, self.H2H_DEFAULT_POSSESSION)
            feats["h2h_avg_home_possession"] = round(
                self.H2H_POSS_CENTER + (raw_poss - self.H2H_POSS_CENTER) * self.H2H_POSS_COMPRESSION, self.DENOM_MIN_1
            )

        feats["h2h_avg_home_dangerous"] = _smooth_mean(home_dang, self.H2H_DEFAULT_DANGER_HOME)
        feats["h2h_avg_away_dangerous"] = _smooth_mean(away_dang, self.H2H_DEFAULT_DANGER_AWAY)

        return feats

    # ────────────────────────────────────────────────────────────
    # 类别 E: 近期状态
    # ────────────────────────────────────────────────────────────

    def _extract_recent_form(self, home_stats, away_stats, features: dict = None, before_date=None) -> dict:
        """类别 E: 近期状态特征"""
        if features is None:
            features = {}

        # 将 before_date（北京时间）转为 UTC 日期字符串用于过滤
        cutoff_date = None
        if before_date:
            from datetime import timedelta
            utc_dt = before_date - timedelta(hours=8) if hasattr(before_date, 'strftime') else before_date
            cutoff_date = utc_dt.strftime("%Y-%m-%d") if hasattr(utc_dt, 'strftime') else str(utc_dt)[:10]

        feats = {}

        def _parse_team_form(stats, prefix: str):
            matches = stats.recent_matches if stats and stats.recent_matches else []
            if not isinstance(matches, list) or len(matches) == self.RANK_DEFAULT_POS:
                for k in [f"{prefix}_form_pts_6", f"{prefix}_form_pts_10",
                          f"{prefix}_gf_avg_6", f"{prefix}_ga_avg_6",
                          f"{prefix}_gf_avg_10", f"{prefix}_ga_avg_10",
                          f"{prefix}_form_trend", f"{prefix}_home_form_pts",
                          f"{prefix}_away_form_pts"]:
                    feats[k] = self.FORM_DEFAULT
                return

            parsed = []
            for m in matches:
                if not isinstance(m, dict):
                    continue
                # V4.12: 过滤掉比赛日当天及之后的记录（防数据泄露）
                if cutoff_date and m.get("date", "") >= cutoff_date:
                    continue
                result = m.get("result", "-")
                score = m.get("score", "?:?")
                is_home = self._parse_recent_is_home(m)

                pts = self.FORM_PTS_WIN if result == "W" else self.FORM_PTS_DRAW if result == "D" else self.RANK_DEFAULT_POS
                gf, ga = self._parse_recent_score(score)

                parsed.append({"pts": pts, "gf": gf, "ga": ga, "is_home": is_home})

            if not parsed:
                for k in [f"{prefix}_form_pts_6", f"{prefix}_form_pts_10",
                          f"{prefix}_gf_avg_6", f"{prefix}_ga_avg_6",
                          f"{prefix}_gf_avg_10", f"{prefix}_ga_avg_10",
                          f"{prefix}_form_trend", f"{prefix}_home_form_pts",
                          f"{prefix}_away_form_pts"]:
                    feats[k] = self.FORM_DEFAULT
                return

            n = len(parsed)
            window6 = parsed[:min(self.FORM_WINDOW_6, n)]
            window10 = parsed[:min(self.FORM_WINDOW_10, n)]

            feats[f"{prefix}_form_pts_6"] = sum(m["pts"] for m in window6) / len(window6)
            feats[f"{prefix}_form_pts_10"] = sum(m["pts"] for m in window10) / len(window10)

            n6 = len(window6)
            raw_gf = sum(m["gf"] for m in window6) / n6
            raw_ga = sum(m["ga"] for m in window6) / n6

            raw_gf = min(raw_gf, self.FORM_GF_WINSORIZE_CAP)
            raw_ga = min(raw_ga, self.FORM_GF_WINSORIZE_CAP)

            games_played = features.get(f"{prefix}_games_played", self.FORM_WINDOW_10)
            base_strength = self.FORM_PRIOR_BASE_STRENGTH + max(self.RANK_DEFAULT_POS, self.FORM_PRIOR_GAMES_OFFSET - games_played)
            prior_strength = min(base_strength, self.FORM_PRIOR_MAX_STRENGTH)
            shrinkage = prior_strength / (prior_strength + n6)
            feats[f"{prefix}_gf_avg_6"] = round(shrinkage * self.FORM_GF_PRIOR + (self.SAME_LEAGUE_YES - shrinkage) * raw_gf, self.FORM_TREND_MIN_GAMES)
            feats[f"{prefix}_ga_avg_6"] = round(shrinkage * self.FORM_GA_PRIOR + (self.SAME_LEAGUE_YES - shrinkage) * raw_ga, self.FORM_TREND_MIN_GAMES)

            # 近10场：与近6场同逻辑，window 不同
            n10 = len(window10)
            raw_gf10 = sum(m["gf"] for m in window10) / n10
            raw_ga10 = sum(m["ga"] for m in window10) / n10
            raw_gf10 = min(raw_gf10, self.FORM_GF_WINSORIZE_CAP)
            raw_ga10 = min(raw_ga10, self.FORM_GF_WINSORIZE_CAP)
            shrinkage10 = prior_strength / (prior_strength + n10)
            feats[f"{prefix}_gf_avg_10"] = round(shrinkage10 * self.FORM_GF_PRIOR + (self.SAME_LEAGUE_YES - shrinkage10) * raw_gf10, self.FORM_TREND_MIN_GAMES)
            feats[f"{prefix}_ga_avg_10"] = round(shrinkage10 * self.FORM_GA_PRIOR + (self.SAME_LEAGUE_YES - shrinkage10) * raw_ga10, self.FORM_TREND_MIN_GAMES)

            if n >= self.FORM_TREND_MIN_GAMES:
                mid = n // self.H2H_POINTS_DRAW
                recent_half = parsed[:mid]
                older_half = parsed[mid:mid * self.H2H_POINTS_DRAW]
                feats[f"{prefix}_form_trend"] = (
                    sum(m["pts"] for m in recent_half) / max(len(recent_half), self.DENOM_MIN_1) -
                    sum(m["pts"] for m in older_half) / max(len(older_half), self.DENOM_MIN_1)
                )
            else:
                feats[f"{prefix}_form_trend"] = self.FORM_DEFAULT

            home_matches = [m for m in window6 if m["is_home"]]
            away_matches = [m for m in window6 if not m["is_home"]]
            feats[f"{prefix}_home_form_pts"] = (
                sum(m["pts"] for m in home_matches) / max(len(home_matches), self.DENOM_MIN_1)
                if home_matches else self.FORM_DEFAULT
            )
            feats[f"{prefix}_away_form_pts"] = (
                sum(m["pts"] for m in away_matches) / max(len(away_matches), self.DENOM_MIN_1)
                if away_matches else self.FORM_DEFAULT
            )

        _parse_team_form(home_stats, "home")
        _parse_team_form(away_stats, "away")
        return feats

    # ────────────────────────────────────────────────────────────
    # 类别 C: 赔率信号 V4
    # ────────────────────────────────────────────────────────────

    def _extract_odds(self, odds_data: dict) -> dict:
        """类别 C: 赔率信号特征 V4

        包含：1X2 欧赔 / 亚盘 / 大小球 / 共识度
        """
        feats = {
            "odds_home_current": self.ODDS_DEFAULT_VAL, "odds_draw_current": self.ODDS_DEFAULT_VAL, "odds_away_current": self.ODDS_DEFAULT_VAL,
            "odds_home_initial": self.ODDS_DEFAULT_VAL, "odds_draw_initial": self.ODDS_DEFAULT_VAL, "odds_away_initial": self.ODDS_DEFAULT_VAL,
            "odds_movement_home": self.ODDS_DEFAULT_VAL, "odds_movement_draw": self.ODDS_DEFAULT_VAL, "odds_movement_away": self.ODDS_DEFAULT_VAL,
            "odds_change_pct_home": self.ODDS_DEFAULT_VAL, "odds_change_pct_draw": self.ODDS_DEFAULT_VAL, "odds_change_pct_away": self.ODDS_DEFAULT_VAL,
            "odds_implied_home_change": self.ODDS_DEFAULT_VAL, "draw_odds_current": self.ODDS_DEFAULT_VAL,
            "odds_std_home": self.ODDS_DEFAULT_VAL, "odds_std_draw": self.ODDS_DEFAULT_VAL, "odds_std_away": self.ODDS_DEFAULT_VAL,
            "odds_dispersity": self.ODDS_DEFAULT_VAL,
            "odds_market_home_prob": self.ODDS_DEFAULT_PROB, "odds_market_draw_prob": self.ODDS_DEFAULT_PROB, "odds_market_away_prob": self.ODDS_DEFAULT_PROB,
            "handicap_home_current": self.ODDS_DEFAULT_VAL, "handicap_away_current": self.ODDS_DEFAULT_VAL,
            "handicap_line_market": self.ODDS_DEFAULT_VAL, "handicap_line_jc_diff": self.ODDS_DEFAULT_VAL,
            "handicap_home_movement": self.ODDS_DEFAULT_VAL, "handicap_away_movement": self.ODDS_DEFAULT_VAL,
            "handicap_line_shift": self.ODDS_DEFAULT_VAL,
            "handicap_implied_home_prob": self.ODDS_DEFAULT_VAL, "handicap_implied_away_prob": self.ODDS_DEFAULT_VAL,
            "goal_line_market": self.ODDS_DEFAULT_VAL, "goal_line_jc_diff": self.ODDS_DEFAULT_VAL,
            "over_odds_current": self.ODDS_DEFAULT_VAL, "under_odds_current": self.ODDS_DEFAULT_VAL,
            "over_odds_movement": self.ODDS_DEFAULT_VAL, "under_odds_movement": self.ODDS_DEFAULT_VAL,
            "goal_line_change": self.ODDS_DEFAULT_VAL,
            "goal_line_max": self.ODDS_DEFAULT_VAL, "goal_line_min": self.ODDS_DEFAULT_VAL,
            "goal_line_drop_from_peak": self.ODDS_DEFAULT_VAL,
            "over_odds_decline_rate": self.ODDS_DEFAULT_VAL,
            "goal_line_volatility": self.ODDS_DEFAULT_VAL,
            "odds_consensus_direction": self.ODDS_DEFAULT_VAL, "handicap_consensus_direction": self.ODDS_DEFAULT_VAL,
            "odds_divergence_trend": self.ODDS_DEFAULT_VAL, "handicap_divergence_trend": self.ODDS_DEFAULT_VAL,
            "bookmaker_count": self.RANK_DEFAULT_POS, "odds_time_depth": self.ODDS_TIME_DEPTH,
        }

        if not odds_data.get("has_data"):
            return feats

        by_bm = odds_data["by_bookmaker"]
        latest = odds_data["latest"]
        prev_snaps = odds_data["prev"]
        times = odds_data["times"]
        all_odds_list = [o for snaps in by_bm.values() for o in snaps]
        feats["bookmaker_count"] = odds_data["bookmaker_count"]
        feats["odds_time_depth"] = self.ODDS_TIME_DEPTH

        # ---- 1X2 欧赔 ----
        if latest:
            feats["odds_home_current"] = self._safe_mean([o.home_win for o in latest])
            feats["odds_draw_current"] = self._safe_mean([o.draw for o in latest])
            feats["odds_away_current"] = self._safe_mean([o.away_win for o in latest])
            feats["draw_odds_current"] = feats["odds_draw_current"]

        if len(latest) >= self.ODDS_MIN_SAMPLES_STD:
            feats["odds_std_home"] = self._safe_std([o.home_win for o in latest])
            feats["odds_std_draw"] = self._safe_std([o.draw for o in latest])
            feats["odds_std_away"] = self._safe_std([o.away_win for o in latest])
            feats["odds_dispersity"] = max(feats["odds_std_home"], feats["odds_std_draw"], feats["odds_std_away"])

        movements_h, movements_d, movements_a = [], [], []
        pct_h, pct_d, pct_a = [], [], []
        initial_h, initial_d, initial_a = [], [], []

        for bm, snaps in by_bm.items():
            if len(snaps) < self.DENOM_MIN_1:
                continue
            first = snaps[self.RANK_DEFAULT_POS]
            last = snaps[-self.DENOM_MIN_1]

            initial_h.append(first.home_win)
            initial_d.append(first.draw)
            initial_a.append(first.away_win)

            if len(snaps) >= self.ODDS_MIN_SAMPLES_STD:
                fh = first.home_win or self.RANK_DEFAULT_POS
                lh = last.home_win or self.RANK_DEFAULT_POS
                fd = first.draw or self.RANK_DEFAULT_POS
                ld = last.draw or self.RANK_DEFAULT_POS
                fa = first.away_win or self.RANK_DEFAULT_POS
                la = last.away_win or self.RANK_DEFAULT_POS

                movements_h.append(fh - lh)
                movements_d.append(fd - ld)
                movements_a.append(fa - la)

                if fh > self.RANK_DEFAULT_POS:
                    pct_h.append((fh - lh) / fh)
                if fd > self.RANK_DEFAULT_POS:
                    pct_d.append((fd - ld) / fd)
                if fa > self.RANK_DEFAULT_POS:
                    pct_a.append((fa - la) / fa)
            else:
                movements_h.append(self.ODDS_DEFAULT_VAL)
                movements_d.append(self.ODDS_DEFAULT_VAL)
                movements_a.append(self.ODDS_DEFAULT_VAL)
                pct_h.append(self.ODDS_DEFAULT_VAL)
                pct_d.append(self.ODDS_DEFAULT_VAL)
                pct_a.append(self.ODDS_DEFAULT_VAL)

        if initial_h:
            feats["odds_home_initial"] = self._safe_mean(initial_h)
            feats["odds_draw_initial"] = self._safe_mean(initial_d)
            feats["odds_away_initial"] = self._safe_mean(initial_a)

        if movements_h:
            feats["odds_movement_home"] = self._safe_mean(movements_h)
            feats["odds_movement_draw"] = self._safe_mean(movements_d)
            feats["odds_movement_away"] = self._safe_mean(movements_a)
            feats["odds_change_pct_home"] = self._safe_mean(pct_h)
            feats["odds_change_pct_draw"] = self._safe_mean(pct_d)
            feats["odds_change_pct_away"] = self._safe_mean(pct_a)

            hi = self.SAME_LEAGUE_YES / max(feats["odds_home_initial"], self.ODDS_DENOM_MIN)
            hc = self.SAME_LEAGUE_YES / max(feats["odds_home_current"], self.ODDS_DENOM_MIN) if feats["odds_home_current"] > self.RANK_DEFAULT_POS else self.RANK_DEFAULT_POS
            feats["odds_implied_home_change"] = hc - hi

        if prev_snaps and len(prev_snaps) >= self.ODDS_MIN_SAMPLES_STD and len(latest) >= self.ODDS_MIN_SAMPLES_STD:
            prev_std = self._safe_std([o.home_win for o in prev_snaps])
            cur_std = feats["odds_std_home"]
            feats["odds_divergence_trend"] = cur_std - prev_std

        h, d, a = feats["odds_home_current"], feats["odds_draw_current"], feats["odds_away_current"]
        if h > self.RANK_DEFAULT_POS and d > self.RANK_DEFAULT_POS and a > self.RANK_DEFAULT_POS:
            imp_h = self.SAME_LEAGUE_YES / h
            imp_d = self.SAME_LEAGUE_YES / d
            imp_a = self.SAME_LEAGUE_YES / a
            total = imp_h + imp_d + imp_a
            if total > self.RANK_DEFAULT_POS:
                feats["odds_market_home_prob"] = imp_h / total
                feats["odds_market_draw_prob"] = imp_d / total
                feats["odds_market_away_prob"] = imp_a / total

        # ---- 亚盘 ----
        best_line = None
        best_balance = self.HCP_BEST_BALANCE_INIT
        for o in latest:
            hl = o.handicap_line
            hh = o.handicap_home
            ha = o.handicap_away
            if hl is None or hh is None or ha is None:
                continue
            balance = abs(hh - ha)
            if balance < best_balance:
                best_balance = balance
                best_line = hl
        consensus_line = best_line

        hcp_at_line = [o for o in latest
                       if o.handicap_line == consensus_line
                       and o.handicap_home is not None and o.handicap_away is not None]
        if hcp_at_line:
            feats["handicap_line_market"] = float(consensus_line)
            feats["handicap_home_current"] = self._safe_mean([o.handicap_home for o in hcp_at_line])
            feats["handicap_away_current"] = self._safe_mean([o.handicap_away for o in hcp_at_line])

            hh = feats["handicap_home_current"]
            ha = feats["handicap_away_current"]
            if hh > self.RANK_DEFAULT_POS and ha > self.RANK_DEFAULT_POS:
                imp_hh = self.SAME_LEAGUE_YES / hh
                imp_ha = self.SAME_LEAGUE_YES / ha
                total_hcp = imp_hh + imp_ha
                if total_hcp > self.RANK_DEFAULT_POS:
                    feats["handicap_implied_home_prob"] = imp_hh / total_hcp
                    feats["handicap_implied_away_prob"] = imp_ha / total_hcp

        hcp_move_h, hcp_move_a, hcp_line_shift = [], [], []
        for bm, snaps in by_bm.items():
            hcp_snaps = [s for s in snaps if s.handicap_home is not None and s.handicap_line == consensus_line]
            if len(hcp_snaps) >= self.ODDS_MIN_SAMPLES_STD:
                f = hcp_snaps[self.RANK_DEFAULT_POS]
                l = hcp_snaps[-self.DENOM_MIN_1]
                hcp_move_h.append((f.handicap_home or self.RANK_DEFAULT_POS) - (l.handicap_home or self.RANK_DEFAULT_POS))
                hcp_move_a.append((f.handicap_away or self.RANK_DEFAULT_POS) - (l.handicap_away or self.RANK_DEFAULT_POS))
                hcp_line_shift.append((l.handicap_line or self.RANK_DEFAULT_POS) - (f.handicap_line or self.RANK_DEFAULT_POS))
            elif len(hcp_snaps) == self.DENOM_MIN_1:
                hcp_move_h.append(self.ODDS_DEFAULT_VAL)
                hcp_move_a.append(self.ODDS_DEFAULT_VAL)
                hcp_line_shift.append(self.ODDS_DEFAULT_VAL)

        if hcp_move_h:
            feats["handicap_home_movement"] = self._safe_mean(hcp_move_h)
            feats["handicap_away_movement"] = self._safe_mean(hcp_move_a)
            feats["handicap_line_shift"] = self._safe_mean(hcp_line_shift)

        if prev_snaps and consensus_line is not None:
            prev_hcp = [o for o in prev_snaps
                        if o.handicap_line == consensus_line and o.handicap_home is not None]
            cur_hcp = hcp_at_line
            if len(prev_hcp) >= self.ODDS_MIN_SAMPLES_STD and len(cur_hcp) >= self.ODDS_MIN_SAMPLES_STD:
                prev_hcp_std = self._safe_std([o.handicap_home for o in prev_hcp])
                cur_hcp_std = self._safe_std([o.handicap_home for o in cur_hcp])
                feats["handicap_divergence_trend"] = cur_hcp_std - prev_hcp_std

        # ---- 大小球 ----
        all_ou_times = []
        for t in times:
            snaps_at_t = [o for o in all_odds_list if o.snapshot_time == t]
            for o in snaps_at_t:
                if o.goal_line is not None and (o.over_odds is not None or o.under_odds is not None):
                    all_ou_times.append((t, o.goal_line, o.over_odds, o.under_odds, o.bookmaker))

        if all_ou_times:
            # V4.12: 过滤非标准盘口，仅保留合理范围 [1.5, 3.5] 的大小球线
            valid_ou_times = [
                (t, gl, ov, un, bm) for (t, gl, ov, un, bm) in all_ou_times
                if self.GOAL_LINE_MIN_VALID <= gl <= self.GOAL_LINE_MAX_VALID
            ]

            # 取有 goal_line 数据的最后时间（而非所有 bookmaker 的最后时间）
            ou_times_sorted = sorted(set(t for (t, gl, ov, un, bm) in all_ou_times))
            latest_t = ou_times_sorted[-1] if ou_times_sorted else None

            # 从最新快照中取合理范围内的众数盘口
            latest_gls = [gl for (t, gl, ov, un, bm) in valid_ou_times if t == latest_t]
            gl_counter = Counter(latest_gls)
            best_gl = None
            if gl_counter:
                candidate_gl, candidate_count = gl_counter.most_common(self.DENOM_MIN_1)[self.RANK_DEFAULT_POS]
                if candidate_count >= self.GOAL_LINE_MIN_COUNT:
                    best_gl = candidate_gl
                else:
                    # 最新快照中合理范围内 bookmaker 不足，回退到全部快照的众数
                    all_valid_gls = [gl for (t, gl, ov, un, bm) in valid_ou_times]
                    all_gl_counter = Counter(all_valid_gls)
                    if all_gl_counter:
                        best_gl = all_gl_counter.most_common(self.DENOM_MIN_1)[self.RANK_DEFAULT_POS][self.RANK_DEFAULT_POS]
            else:
                # 最新快照无有效范围 goal_line（如 1xbet 盘口 1.0 被过滤），回退全局众数
                all_valid_gls = [gl for (t, gl, ov, un, bm) in valid_ou_times]
                all_gl_counter = Counter(all_valid_gls)
                if all_gl_counter:
                    best_gl = all_gl_counter.most_common(self.DENOM_MIN_1)[self.RANK_DEFAULT_POS][self.RANK_DEFAULT_POS]

            if best_gl:
                feats["goal_line_market"] = float(best_gl)

                cur_overs = [ov for (t, gl, ov, un, bm) in valid_ou_times
                             if t == latest_t and gl == best_gl and ov is not None]
                cur_unders = [un for (t, gl, ov, un, bm) in valid_ou_times
                              if t == latest_t and gl == best_gl and un is not None]
                feats["over_odds_current"] = self._safe_mean(cur_overs)
                feats["under_odds_current"] = self._safe_mean(cur_unders)

            all_gls = [gl for (t, gl, ov, un, bm) in valid_ou_times]
            feats["goal_line_max"] = max(all_gls) if all_gls else self.ODDS_DEFAULT_VAL
            feats["goal_line_min"] = min(all_gls) if all_gls else self.ODDS_DEFAULT_VAL
            feats["goal_line_volatility"] = self._safe_std(all_gls)

            if best_gl and feats["goal_line_max"] > self.RANK_DEFAULT_POS:
                feats["goal_line_drop_from_peak"] = feats["goal_line_max"] - float(best_gl)

            over_moves, under_moves, gl_moves = [], [], []
            for bm, snaps in by_bm.items():
                ou_snaps = [(s.snapshot_time, s.goal_line, s.over_odds, s.under_odds)
                            for s in snaps if s.goal_line is not None
                            and (s.over_odds is not None or s.under_odds is not None)]
                if len(ou_snaps) >= self.ODDS_MIN_SAMPLES_STD:
                    first = ou_snaps[self.RANK_DEFAULT_POS]
                    last = ou_snaps[-self.DENOM_MIN_1]
                    if first[self.H2H_POINTS_DRAW] is not None and last[self.H2H_POINTS_DRAW] is not None:
                        over_moves.append(first[self.H2H_POINTS_DRAW] - last[self.H2H_POINTS_DRAW])
                    if first[self.H2H_POINTS_WIN] is not None and last[self.H2H_POINTS_WIN] is not None:
                        under_moves.append(first[self.H2H_POINTS_WIN] - last[self.H2H_POINTS_WIN])
                    if first[self.DENOM_MIN_1] is not None and last[self.DENOM_MIN_1] is not None:
                        gl_moves.append(last[self.DENOM_MIN_1] - first[self.DENOM_MIN_1])

            feats["over_odds_movement"] = self._safe_mean(over_moves)
            feats["under_odds_movement"] = self._safe_mean(under_moves)
            feats["goal_line_change"] = self._safe_mean(gl_moves)

            over_time_series = sorted(set(
                (t, ov) for (t, gl, ov, un, bm) in all_ou_times
                if ov is not None and gl == best_gl
            ), key=lambda x: x[self.RANK_DEFAULT_POS])
            if len(over_time_series) >= self.ODDS_MIN_SAMPLES_STD and best_gl:
                cutoff = over_time_series[-self.DENOM_MIN_1][self.RANK_DEFAULT_POS] - timedelta(hours=self.OU_DECLINE_WINDOW_HOURS)
                recent = [(t, ov) for (t, ov) in over_time_series if t >= cutoff]
                if len(recent) >= self.ODDS_MIN_SAMPLES_STD:
                    first_t, first_ov = recent[self.RANK_DEFAULT_POS]
                    last_t, last_ov = recent[-self.DENOM_MIN_1]
                    hours = max((last_t - first_t).total_seconds() / self.OU_SECONDS_PER_HOUR, self.OU_DECLINE_MIN_HOURS)
                    rate = (first_ov - last_ov) / hours
                    if first_ov > self.RANK_DEFAULT_POS:
                        feats["over_odds_decline_rate"] = rate / first_ov
                    else:
                        feats["over_odds_decline_rate"] = rate

        # ---- 共识度 ----
        if movements_h:
            signs = [self.POINTS_PER_DRAW if m > self.ODDS_HCP_SIGN_THRESHOLD else (self.POINTS_PER_DRAW * -1 if m < self.ODDS_HCP_SIGN_NEG_THRESHOLD else self.RANK_DEFAULT_POS) for m in movements_h]
            if signs:
                feats["odds_consensus_direction"] = self._safe_mean(signs)

        if hcp_move_h:
            hcp_signs = [self.POINTS_PER_DRAW if m > self.ODDS_HCP_SIGN_THRESHOLD else (self.POINTS_PER_DRAW * -1 if m < self.ODDS_HCP_SIGN_NEG_THRESHOLD else self.RANK_DEFAULT_POS) for m in hcp_move_h]
            if hcp_signs:
                feats["handicap_consensus_direction"] = self._safe_mean(hcp_signs)

        return feats

    # ────────────────────────────────────────────────────────────
    # 类别 C++: JC 与市场偏离
    # ────────────────────────────────────────────────────────────

    def _compute_contextual_deviation(self, features: dict) -> dict:
        """计算 JC 与市场之间的偏离特征"""
        extra = {
            "handicap_line_jc_diff": self.JC_DIFF_DEFAULT,
            "goal_line_jc_diff": self.JC_DIFF_DEFAULT,
        }

        jc_handicap = features.get("handicap_line", self.JC_DIFF_DEFAULT)
        market_handicap = features.get("handicap_line_market", self.JC_DIFF_DEFAULT)
        if market_handicap != self.JC_DIFF_DEFAULT:
            extra["handicap_line_jc_diff"] = jc_handicap - market_handicap

        return extra

    # ────────────────────────────────────────────────────────────
    # L3 偏离度
    # ────────────────────────────────────────────────────────────

    def _compute_deviation(self, features: dict) -> dict:
        """L3 偏离度：市场隐含概率 vs 纯数据基线概率"""
        market_h = features.get("odds_market_home_prob", self.DEV_DEFAULT_PROB)
        market_d = features.get("odds_market_draw_prob", self.DEV_DEFAULT_PROB)
        market_a = features.get("odds_market_away_prob", self.DEV_DEFAULT_PROB)

        base_h = features.get("home_win_rate", self.DEV_DEFAULT_PROB)
        base_a = features.get("away_win_rate", self.DEV_DEFAULT_PROB)
        base_d = self.DEV_DEFAULT_DRAW

        dev_h = market_h - base_h
        dev_d = market_d - base_d
        dev_a = market_a - base_a

        return {
            "deviation_home": round(dev_h, self.FORM_WINDOW_6),
            "deviation_draw": round(dev_d, self.FORM_WINDOW_6),
            "deviation_away": round(dev_a, self.FORM_WINDOW_6),
            "deviation_abs_max": round(max(abs(dev_h), abs(dev_d), abs(dev_a)), self.FORM_WINDOW_6),
            "deviation_home_sign": self.SAME_LEAGUE_YES if dev_h > self.FORM_DEFAULT else (-self.SAME_LEAGUE_YES if dev_h < self.FORM_DEFAULT else self.FORM_DEFAULT),
            "deviation_direction": self.SAME_LEAGUE_YES if abs(dev_h) >= abs(dev_a) else -self.SAME_LEAGUE_YES,
        }

    # ────────────────────────────────────────────────────────────
    # 类别 A++: 联赛归一化
    # ────────────────────────────────────────────────────────────

    def _normalize_by_league(self, features: dict, baseline: dict, home_stats, away_stats) -> dict:
        """将球队原始统计除以联赛均值，实现跨联赛可比"""
        if not baseline:
            return {}

        feats = {}
        lahg = baseline.get("league_avg_home_goals", self.NORM_AVG_HOME_GOALS) or self.NORM_AVG_HOME_GOALS
        laag = baseline.get("league_avg_away_goals", self.NORM_AVG_AWAY_GOALS) or self.NORM_AVG_AWAY_GOALS
        lhw = baseline.get("league_home_win_rate", self.NORM_HOME_WIN_RATE) or self.NORM_HOME_WIN_RATE
        ldw = baseline.get("league_draw_rate", self.NORM_DRAW_RATE) or self.NORM_DRAW_RATE
        ltg = baseline.get("league_avg_total_goals", self.NORM_AVG_TOTAL_GOALS) or self.NORM_AVG_TOTAL_GOALS

        feats["home_win_rate_norm"] = features.get("home_win_rate", self.RANK_DEFAULT_POS) / max(lhw, self.NORM_DENOM_MIN)
        feats["home_goals_avg_norm"] = features.get("home_goals_avg", self.RANK_DEFAULT_POS) / max(lahg, self.NORM_DENOM_MIN)
        feats["home_goals_against_avg_norm"] = features.get("home_goals_against_avg", self.RANK_DEFAULT_POS) / max(laag, self.NORM_DENOM_MIN)

        away_win_rate = features.get("away_win_rate", self.RANK_DEFAULT_POS)
        feats["away_win_rate_norm"] = away_win_rate / max(self.SAME_LEAGUE_YES - lhw - ldw, self.NORM_DENOM_MIN)
        feats["away_goals_avg_norm"] = features.get("away_goals_avg", self.RANK_DEFAULT_POS) / max(laag, self.NORM_DENOM_MIN)
        feats["away_goals_against_avg_norm"] = features.get("away_goals_against_avg", self.RANK_DEFAULT_POS) / max(lahg, self.NORM_DENOM_MIN)

        feats["home_xg_norm"] = features.get("home_xG", self.RANK_DEFAULT_POS) / max(ltg / self.H2H_POINTS_DRAW, self.NORM_DENOM_MIN)
        feats["away_xg_norm"] = features.get("away_xG", self.RANK_DEFAULT_POS) / max(ltg / self.H2H_POINTS_DRAW, self.NORM_DENOM_MIN)

        feats["league_avg_total_goals"] = ltg
        feats["league_home_win_rate"] = lhw

        if home_stats and home_stats.avg_possession:
            feats["home_possession_tendency"] = float(home_stats.avg_possession) / self.NORM_POSSESSION_FACTOR
        else:
            feats["home_possession_tendency"] = self.NORM_DEFAULT_POSS

        if away_stats and away_stats.avg_possession:
            feats["away_possession_tendency"] = float(away_stats.avg_possession) / self.NORM_POSSESSION_FACTOR
        else:
            feats["away_possession_tendency"] = self.NORM_DEFAULT_POSS

        feats["style_clash_possession"] = abs(
            feats["home_possession_tendency"] - feats["away_possession_tendency"]
        )

        home_xg = features.get("home_xG", self.RANK_DEFAULT_POS) or 0
        away_xg = features.get("away_xG", self.RANK_DEFAULT_POS) or 0
        # V4.12: xG<0.3 时数据不可靠，efficiency 取 1.0 中性值
        home_g = features.get("home_goals_avg", 1.0)
        away_g = features.get("away_goals_avg", 1.0)
        if home_xg > 0.3:
            feats["home_attacking_efficiency"] = home_g / max(home_xg, self.NORM_EFFICIENCY_DENOM)
        else:
            feats["home_attacking_efficiency"] = 1.0
        if away_xg > 0.3:
            feats["away_attacking_efficiency"] = away_g / max(away_xg, self.NORM_EFFICIENCY_DENOM)
        else:
            feats["away_attacking_efficiency"] = 1.0

        feats["home_defensive_index"] = features.get("home_goals_against_avg", self.RANK_DEFAULT_POS) / max(features.get("home_xGA", self.NORM_DEFENSIVE_DEFAULT_XGA), self.NORM_EFFICIENCY_DENOM)
        feats["away_defensive_index"] = features.get("away_goals_against_avg", self.RANK_DEFAULT_POS) / max(features.get("away_xGA", self.NORM_DEFENSIVE_DEFAULT_XGA), self.NORM_EFFICIENCY_DENOM)

        return feats

    # ────────────────────────────────────────────────────────────
    # 类别 A+++: 联赛排名
    # ────────────────────────────────────────────────────────────

    def _extract_rank_features(self, home_stats, away_stats, league_baseline: dict, features: dict = None) -> dict:
        """联赛排名特征（跨联赛可比：用排名分位而非绝对排名）"""
        if features is None:
            features = {}
        feats = {
            "home_league_position": self.RANK_DEFAULT_POS,
            "away_league_position": self.RANK_DEFAULT_POS,
            "home_rank_percentile": self.RANK_DEFAULT_PERCENTILE,
            "away_rank_percentile": self.RANK_DEFAULT_PERCENTILE,
            "rank_percentile_diff": self.RANK_DEFAULT_DIFF,
        }

        total_teams = league_baseline.get("league_count", self.RANK_LEAGUE_COUNT_DEFAULT) / self.RANK_LEAGUE_COUNT_DIVISOR if league_baseline else self.RANK_TOTAL_TEAMS_MIN + self.RANK_TOTAL_TEAMS_MIN // self.H2H_POINTS_DRAW
        total_teams = max(total_teams, self.RANK_TOTAL_TEAMS_MIN)

        lhw = league_baseline.get("league_home_win_rate", self.RANK_PPG_LHW) if league_baseline else self.RANK_PPG_LHW
        ld = league_baseline.get("league_draw_rate", self.RANK_PPG_LD) if league_baseline else self.RANK_PPG_LD
        league_avg_ppg = lhw * self.POINTS_PER_WIN + ld

        def _estimate_percentile(ppg, avg_ppg, total):
            if ppg <= self.RANK_DEFAULT_POS or avg_ppg <= self.RANK_DEFAULT_POS:
                return self.RANK_DEFAULT_PERCENTILE
            ratio = ppg / avg_ppg
            percentile = self.SAME_LEAGUE_YES / (self.SAME_LEAGUE_YES + math.exp(-self.RANK_SIGMOID_SCALE * (ratio - self.RANK_SIGMOID_CENTER)))
            return max(self.RANK_PERCENTILE_MIN, min(self.RANK_PERCENTILE_MAX, percentile))

        if home_stats and home_stats.league_position:
            pos = home_stats.league_position
            feats["home_league_position"] = float(pos)
            feats["home_rank_percentile"] = self.SAME_LEAGUE_YES - (pos - self.SAME_LEAGUE_YES) / total_teams
        else:
            home_ppg = features.get("home_points_per_game", league_avg_ppg)
            feats["home_rank_percentile"] = _estimate_percentile(home_ppg, league_avg_ppg, total_teams)

        if away_stats and away_stats.league_position:
            pos = away_stats.league_position
            feats["away_league_position"] = float(pos)
            feats["away_rank_percentile"] = self.SAME_LEAGUE_YES - (pos - self.SAME_LEAGUE_YES) / total_teams
        else:
            away_ppg = features.get("away_points_per_game", league_avg_ppg)
            feats["away_rank_percentile"] = _estimate_percentile(away_ppg, league_avg_ppg, total_teams)

        feats["rank_percentile_diff"] = feats["home_rank_percentile"] - feats["away_rank_percentile"]

        return feats

    # ────────────────────────────────────────────────────────────
    # 类别 F: SM 官方预测
    # ────────────────────────────────────────────────────────────

    def _extract_sm_predictions(self, sm_prediction, features: dict = None) -> dict:
        """SportMonks 官方预测特征（第三方 ensemble 信号）"""
        use_market = features and features.get("odds_market_home_prob", self.FORM_DEFAULT) > self.SM_MARKET_THRESHOLD
        feats = {
            "sm_home_prob": features.get("odds_market_home_prob", self.SM_DEFAULT_PROB) if use_market else self.SM_DEFAULT_PROB,
            "sm_draw_prob": features.get("odds_market_draw_prob", self.SM_DEFAULT_PROB) if use_market else self.SM_DEFAULT_PROB,
            "sm_away_prob": features.get("odds_market_away_prob", self.SM_DEFAULT_PROB) if use_market else self.SM_DEFAULT_PROB,
            "sm_over_2_5_prob": self.SM_DEFAULT_OU,
            "sm_btts_prob": self.SM_DEFAULT_BTTS,
        }

        if not sm_prediction:
            return feats

        items = sm_prediction
        if isinstance(sm_prediction, dict):
            data = sm_prediction.get("data", sm_prediction)
            items = data if isinstance(data, list) else [data]

        if not isinstance(items, list):
            return feats

        for item in items:
            tid = item.get("type_id")
            preds = item.get("predictions", {})

            if tid == 237:
                feats["sm_home_prob"] = float(preds.get("home", self.SM_DEFAULT_PROB)) / self.SM_PROB_DIVISOR
                feats["sm_draw_prob"] = float(preds.get("draw", self.SM_DEFAULT_PROB)) / self.SM_PROB_DIVISOR
                feats["sm_away_prob"] = float(preds.get("away", self.SM_DEFAULT_PROB)) / self.SM_PROB_DIVISOR
            elif tid == 234:
                feats["sm_over_2_5_prob"] = float(preds.get("yes", self.SM_DEFAULT_OU)) / self.SM_PROB_DIVISOR
            elif tid == 231:
                feats["sm_btts_prob"] = float(preds.get("yes", self.SM_DEFAULT_BTTS)) / self.SM_PROB_DIVISOR

        return feats

    # ────────────────────────────────────────────────────────────
    # 类别 A++++: 欧战经验
    # ────────────────────────────────────────────────────────────

    def _extract_uefa_features(self, home_uefa: dict, away_uefa: dict) -> dict:
        """欧战经验特征"""
        feats = {
            "home_uefa_matches": self.UEFA_DEFAULT_MATCHES,
            "home_uefa_win_rate": self.UEFA_DEFAULT_RATE,
            "home_uefa_goal_diff_per_game": self.UEFA_DEFAULT_RATE,
            "away_uefa_matches": self.UEFA_DEFAULT_MATCHES,
            "away_uefa_win_rate": self.UEFA_DEFAULT_RATE,
            "away_uefa_goal_diff_per_game": self.UEFA_DEFAULT_RATE,
            "uefa_experience_gap": self.UEFA_DEFAULT_RATE,
        }

        for side, data, prefix in [("home", home_uefa, "home"), ("away", away_uefa, "away")]:
            if not data:
                continue
            n = data.get("uefa_matches", self.UEFA_DEFAULT_MATCHES)
            if n > self.RANK_DEFAULT_POS:
                feats[f"{prefix}_uefa_matches"] = n
                feats[f"{prefix}_uefa_win_rate"] = data.get("uefa_wins", self.RANK_DEFAULT_POS) / n
                gf = data.get("uefa_goals_for", self.RANK_DEFAULT_POS)
                ga = data.get("uefa_goals_against", self.RANK_DEFAULT_POS)
                feats[f"{prefix}_uefa_goal_diff_per_game"] = (gf - ga) / n

        feats["uefa_experience_gap"] = feats["home_uefa_matches"] - feats["away_uefa_matches"]

        return feats
