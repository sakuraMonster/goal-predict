"""
特征工程 Pipeline V4：从数据库提取约 94 个预测特征

V4 新增：
- 类别 C 重构：按博彩公司分组获取真实时序赔率数据
- 类别 C+：亚盘赔率特征（水位、让球线、变动、隐含概率）
- 类别 C++：大小球赔率特征（盘口、水位、变动）
- 类别 C+++：共识度特征（方向一致性、分歧趋势）
- 类别 C++++：JC 与市场盘口偏离

V3 新增：
- 类别 E：近期状态特征（14维）—— form_pts_6/10、gf/ga_avg_6、form_trend、home/away_form_pts

V2 新增：
- 类别 A+：联赛ID、赛季阶段、排名差、积分差、赛程疲劳
- 类别 C+：完整赔率变动（主/平/客）、隐含概率变化、市场分歧度、平赔绝对值
"""
import pandas as pd
import numpy as np
from collections import Counter
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, or_, cast, Integer
from app.db.models import Match, TeamSeasonStats, HeadToHead, OddsSnapshot, Injury


class FeatureEngineer:
    """特征提取引擎 V4"""

    # 联赛基线缓存：{league_id: {home_win_rate, avg_goals, avg_xg, ...}}
    _league_baselines: dict = {}

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _parse_recent_is_home(match_dict: dict) -> bool:
        """兼容两种场地字段"""
        if "is_home" in match_dict and match_dict["is_home"] is not None:
            return bool(match_dict["is_home"])
        venue = match_dict.get("venue", "")
        if isinstance(venue, str):
            return venue.upper() == "H"
        return False

    @staticmethod
    def _parse_recent_score(score_str: str):
        """解析比分 '2:1' → (2, 1)"""
        if not score_str:
            return 0, 0
        try:
            parts = score_str.strip().split(':')
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass
        return 0, 0

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
        # 类别 A: 球队基础战力（V4.1: 缺失时用联赛均值 fallback）
        league_baseline = await self._get_league_baseline(match.league_id)
        features.update(self._extract_team_strength(home_stats, away_stats, league_baseline))
        # 类别 A+: 上下文特征（V2 新增）
        features.update(self._extract_contextual(match, home_stats, away_stats))
        # 类别 A+++: 联赛排名特征（V4 新增）
        features.update(self._extract_rank_features(home_stats, away_stats, league_baseline, features))
        # 类别 A++++: 欧战经验特征（V4 新增）
        home_uefa = await self._get_uefa_experience(match.home_team_id) if match.home_team_id else {}
        away_uefa = await self._get_uefa_experience(match.away_team_id) if match.away_team_id else {}
        features.update(self._extract_uefa_features(home_uefa, away_uefa))
        # 类别 B: 交锋记录（V4 修复：按当前比赛方向对齐）
        features.update(self._extract_h2h(h2h, match.home_team_id, match.away_team_id))
        # 类别 C: 赔率信号（V4 重构：按博彩公司分组时序 + 亚盘/大小球/共识度）
        features.update(self._extract_odds(odds_data))
        # 类别 D: 阵容 & 外部
        features["home_injuries"] = home_injuries
        features["away_injuries"] = away_injuries
        # V4.1: 查询真实休息天数（替代硬编码 7.0）
        current_time = match.kickoff_time.replace(tzinfo=None) if match.kickoff_time else None
        features["home_rest_days"] = await self._get_rest_days(match.home_team_id, current_time)
        features["away_rest_days"] = await self._get_rest_days(match.away_team_id, current_time)
        features["rest_days_diff"] = features["home_rest_days"] - features["away_rest_days"]
        # V4.3: 赛季数据与本场比赛是否同联赛（杯赛/国家队时赛季数据参考价值低）
        home_same_league = 1.0 if (match.league_id and home_stats and match.league_id == home_stats.league_id) else 0.0
        away_same_league = 1.0 if (match.league_id and away_stats and match.league_id == away_stats.league_id) else 0.0
        features["season_match_same_league"] = min(home_same_league, away_same_league)
        # 类别 E: 近期状态（V3 新增）
        features.update(self._extract_recent_form(home_stats, away_stats, features, before_date=match.kickoff_time))
        # 类别 C+: 偏离度特征（市场隐含概率 vs 球队数据基线概率）
        features.update(self._compute_deviation(features))
        # 类别 C++: JC 与市场盘口偏离（V4 新增）
        features.update(self._compute_contextual_deviation(features))
        # 类别 F: SM 官方预测（V4 新增，ensemble 信号）
        try:
            features.update(self._extract_sm_predictions(sm_prediction, features))
        except Exception:
            pass  # SM 预测不可用时静默降级

        # V4.1: xG 数据用 H2H 交锋记录填充（全局赛季 xG 无意义，交锋数据更有代表性）
        self._fill_xg_from_h2h(features)

        # 类别 A++: 联赛归一化特征（V4 新增：跨联赛可比性，需在 xG 填充后执行）
        features.update(self._normalize_by_league(features, league_baseline, home_stats, away_stats))

        # V4.10: 基本面-市场背离检测（识别诱盘场景）
        self._detect_inducement(features)
        # V4.2: 庄家意图特征（赔率变动 + 基本面交叉，识别阻盘/诱盘）
        self._add_bookmaker_intent(features)

        return pd.DataFrame([features])

    # ── 数据查询方法 ──

    @staticmethod
    def _fill_xg_from_h2h(features: dict):
        """V4.1: 当全局 xG 缺失时，用 H2H 交锋记录 xG 填充"""
        h2h_home_xg = features.get("h2h_avg_home_xg", 0)
        h2h_away_xg = features.get("h2h_avg_away_xg", 0)

        # 主队 xG：优先用赛季数据，缺失时用 H2H 数据
        if abs(features.get("home_xG", 0)) < 0.01 and h2h_home_xg > 0:
            features["home_xG"] = h2h_home_xg
            # 同步更新 xG 差值
            features["xG_diff"] = features["home_xG"] - features.get("away_xGA", 0)

        # 客队 xGA（客队失球=主队进球能力）：同理
        if abs(features.get("away_xGA", 0)) < 0.01 and h2h_home_xg > 0:
            features["away_xGA"] = h2h_home_xg

        # 客队 xG
        if abs(features.get("away_xG", 0)) < 0.01 and h2h_away_xg > 0:
            features["away_xG"] = h2h_away_xg
            features["xG_diff"] = features.get("home_xG", 0) - features["away_xG"]

        # 主队 xGA（主队失球=客队进球能力）
        if abs(features.get("home_xGA", 0)) < 0.01 and h2h_away_xg > 0:
            features["home_xGA"] = h2h_away_xg

    @staticmethod
    def _detect_inducement(features: dict):
        """V4.10: 基本面-市场背离检测（诱盘场景识别）
        
        核心逻辑：当市场赔率走势与基本面数据方向矛盾时，市场可能在诱导资金。
        - 基本面明显占优的一方，赔率却不降反升 → 诱盘信号，真实方向应追随基本面
        - 基本面弱势的一方，赔率却大幅下降 → 典型的诱盘，应反向操作
        
        返回: fundamental_vs_market_divergence
        - 正值: 基本面看主队但市场在诱盘走客 → 真实方向偏向主队
        - 负值: 基本面看客队但市场在诱盘走主 → 真实方向偏向客队
        - 0: 无显著背离
        """
        # 基本面指标
        home_wr = features.get("home_win_rate", 0.33)
        away_wr = features.get("away_win_rate", 0.33)
        home_rank = features.get("home_league_rank", 0)
        away_rank = features.get("away_league_rank", 0)
        league_rank_gap = features.get("league_rank_gap", 0)  # 正值=主队排名更好
        home_xg = features.get("home_goals_avg", 1.0) - features.get("away_goals_against_avg", 1.0)
        
        # H2H 综合信号：优先进攻数据（xG/射门/控球/危险进攻），stats 缺失时回退比分数据
        # 跨联赛时 H2H 是唯一直接可比的交手数据，权重会额外提升
        has_h2h = features.get("has_h2h", 0) or 0
        h2h_stats_ok = (features.get("h2h_stats_available", 0) or 0) > 0.5
        h2h_signal = 0.0
        if has_h2h > 0:
            if h2h_stats_ok:
                # 有真实 stats：综合 xG + 射门 + 控球 + 危险进攻
                h2h_xg = features.get("h2h_avg_home_xg", 1.35) - features.get("h2h_avg_away_xg", 1.15)
                h2h_shots = features.get("h2h_avg_shots_ratio", 0.5) - 0.5
                h2h_poss = (features.get("h2h_avg_home_possession", 50) - 50) / 50
                h2h_danger = features.get("h2h_avg_home_dangerous", 45) - features.get("h2h_avg_away_dangerous", 40)
                h2h_signal = (
                    max(-0.5, min(0.5, h2h_xg / 3.0)) * 0.40 +
                    max(-0.4, min(0.4, h2h_shots)) * 0.25 +
                    max(-0.4, min(0.4, h2h_poss)) * 0.15 +
                    max(-0.4, min(0.4, h2h_danger / 100)) * 0.20
                )
            else:
                # stats 缺失：回退到比分数据（进球差 + 胜负率差）
                h2h_goals = features.get("h2h_avg_home_goals", 1.5) - features.get("h2h_avg_away_goals", 1.3)
                h2h_wr = features.get("h2h_home_win_rate", 0.33) - features.get("h2h_away_win_rate", 0.33)
                h2h_signal = (
                    max(-0.5, min(0.5, h2h_goals / 3.0)) * 0.50 +
                    max(-0.5, min(0.5, h2h_wr)) * 0.50
                )
        
        # 综合基本面得分（正值=主队基本面占优）
        fundamental_score = 0.0
        
        # 跨联赛数据可信度折扣
        same_league = features.get("season_match_same_league", 0) or 0
        # same_league=0（跨联赛）→ trust=0.4, same_league=1（同联赛）→ trust=1.0
        data_trust = 0.4 + 0.6 * same_league
        
        # 胜率差（跨联赛数据打折）
        wr_gap = home_wr - away_wr
        if abs(wr_gap) > 0.1:
            fundamental_score += data_trust * 0.25 * (1 if wr_gap > 0 else -1)
        elif abs(wr_gap) > 0.05:
            fundamental_score += data_trust * 0.12 * (1 if wr_gap > 0 else -1)
        
        # 主场优势默认权重（跨联赛时更重要，因为数据不可比）
        # 欧战交叉联赛场景，主场是真实的区位优势
        home_field_weight = 0.15 if same_league >= 0.5 else 0.25
        fundamental_score += home_field_weight  # 主场总是有利
        # 排名差（跨联赛数据打折：不同联赛的排名不可直接对比）
        if home_rank > 0 and away_rank > 0:
            rank_diff = away_rank - home_rank  # 正=主队排名更好
            if abs(rank_diff) > 6:
                fundamental_score += data_trust * 0.25 * (1 if rank_diff > 0 else -1)
            elif abs(rank_diff) > 3:
                fundamental_score += data_trust * 0.12 * (1 if rank_diff > 0 else -1)
        else:
            # 用 league_rank_gap 代替（同受跨联赛折扣）
            if abs(league_rank_gap) > 6:
                fundamental_score += data_trust * 0.25 * (1 if league_rank_gap > 0 else -1)
        # H2H：跨联赛时提升权重（唯一直接可比的进攻数据）
        # same_league=0 → h2h_weight=0.35, same_league=1 → h2h_weight=0.20
        h2h_weight = 0.20 + 0.15 * (1 - same_league)
        if abs(h2h_signal) > 0.05:
            fundamental_score += h2h_weight * (1 if h2h_signal > 0 else -1)
        # xG/gf-ga 差（跨联赛数据打折）
        if abs(home_xg) > 0.3:
            fundamental_score += data_trust * 0.15 * (1 if home_xg > 0 else -1)
        
        # 市场走势（正值=市场在看好主队方向走）
        odds_mv_home = features.get("odds_movement_home", 0) or 0
        odds_mv_away = features.get("odds_movement_away", 0) or 0
        market_score = odds_mv_home - odds_mv_away  # 正值=市场在往主队走
        
        # 背离检测
        # 跨联赛时降低基本面阈值：数据可信度低导致 fundamental_score 被压缩，
        # 但弱方向信号仍有检测价值（尤其 H2H 缺失时）
        fund_threshold = 0.05 if same_league < 0.5 else 0.10
        divergence = 0.0
        if abs(fundamental_score) > fund_threshold and abs(market_score) > 0.02:
            # 基本面与市场方向相反 → 诱盘信号
            fund_sign = 1 if fundamental_score > 0 else -1
            market_sign = 1 if market_score > 0 else -1
            if fund_sign != market_sign:
                # 背离强度 = 基本面强度 × 市场走势强度
                raw_divergence = abs(fundamental_score) * abs(market_score)
                # 归一化：背离越强，反向信号越大
                divergence = fund_sign * raw_divergence * 3.0  # 正值=基本面看主
        
        features["fundamental_vs_market_divergence"] = round(divergence, 4)
        features["fundamental_score"] = round(fundamental_score, 4)
        features["market_direction_score"] = round(market_score, 4)

    @staticmethod
    def _add_bookmaker_intent(features: dict):
        """V4.2: 庄家意图特征——赔率变动幅度 + 基本面交叉
        
        赔率变动分三级（相对初盘的变化率 = movement / initial_odds）：
        - <2%：正常波动，忽略
        - 2-6%：中等变化 → 配合基本面判断阻盘/诱盘
        - >6%：大幅变化 → 强信号，直接作为方向信号
        
        意图规则矩阵（home 方向为正）：
        | 场景                      | 变动  | 意图 | 逻辑                                    |
        | 强队赔率大涨(>6%)          | +2   | -0.8 | 可能有内幕信息，真实看空                  |
        | 强队赔率微升(2-6%)         | +1   | +0.6 | 庄家阻盘，阻挡跟注，实为看好              |
        | 强队赔率微降(2-6%)         | -1   | +0.3 | 市场共识看好                              |
        | 强队赔率大降(>6%)          | -2   | +0.8 | 市场极度看好                              |
        | 弱队赔率大涨(>6%)          | +2   | +0.5 | 弱队被市场放弃，确认对方                  |
        | 弱队赔率微升(2-6%)         | +1   | +0.3 | 市场不看好弱队，确认对方                  |
        | 弱队赔率微降(2-6%)         | -1   | -0.5 | 庄家诱盘，不看好弱队方向                  |
        | 弱队赔率大降(>6%)          | -2   | -0.7 | 强诱盘信号                                |
        | 强弱悬殊+弱队赔率大降(>6%) | -2   | +0.5 | 庄家平衡盘口，反向确认强队                |
        | 强弱悬殊+弱队赔率微降      | -1   | +0.3 | 同理，确认强队                            |
        """
        home_win_rate = features.get("home_win_rate", 0.33)
        home_form = features.get("home_form_pts_6", 0)
        home_ppg = features.get("home_points_per_game", 1.5)
        away_win_rate = features.get("away_win_rate", 0.33)
        away_form = features.get("away_form_pts_6", 0)
        away_ppg = features.get("away_points_per_game", 1.5)
        
        # 赔率变动绝对值
        odds_mv_home = features.get("odds_movement_home", 0)
        odds_mv_away = features.get("odds_movement_away", 0)
        # 初盘赔率（用于计算变动率）
        odds_init_home = features.get("odds_home_initial", 2.0)
        odds_init_away = features.get("odds_away_initial", 2.0)
        
        # 赔率变动率 = -变动值 / 初盘赔率
        # odds_movement = first - last（正=降赔=看好，负=升赔=不看好）
        # 需要取反以统一为"正=升赔=不看好"的行业惯例
        home_change_pct = -odds_mv_home / max(odds_init_home, 0.1) if odds_init_home else 0
        away_change_pct = -odds_mv_away / max(odds_init_away, 0.1) if odds_init_away else 0
        
        # 基本面强度判定
        home_strong = home_win_rate > 0.40 or home_form > 1.6 or home_ppg > 1.8
        away_strong = away_win_rate > 0.40 or away_form > 1.6 or away_ppg > 1.8
        home_weak = home_win_rate < 0.25 and home_form < 1.2
        away_weak = away_win_rate < 0.25 and away_form < 1.2
        
        # 赔率变动分级
        def classify_change(pct):
            """返回: 0=正常波动, 1=中等变化, 2=大幅变化, 符号=方向"""
            ap = abs(pct)
            sign = 1 if pct > 0 else -1 if pct < 0 else 0
            if ap < 0.02: return 0  # 正常波动
            if ap < 0.06: return sign * 1  # 中等变化
            return sign * 2  # 大幅变化
        
        h_change = classify_change(home_change_pct)
        a_change = classify_change(away_change_pct)
        
        # 阻盘信号：强队 + 赔率中等上升(2-6%) → 庄家可能阻盘
        features["bookmaker_block_home"] = 1.0 if (home_strong and h_change == 1) else 0.0
        features["bookmaker_block_away"] = 1.0 if (away_strong and a_change == 1) else 0.0
        
        # 真实看空信号：强队 + 赔率大幅上升(>6%) → 可能有内幕信息
        features["bookmaker_negative_home"] = 1.0 if (home_strong and h_change == 2) else 0.0
        features["bookmaker_negative_away"] = 1.0 if (away_strong and a_change == 2) else 0.0
        
        # 诱盘信号：弱队 + 赔率下降(≥2%) → 庄家可能诱盘
        features["bookmaker_lure_home"] = 1.0 if (home_weak and h_change in (-1, -2)) else 0.0
        features["bookmaker_lure_away"] = 1.0 if (away_weak and a_change in (-1, -2)) else 0.0
        
        # 强弱悬殊判定（用于解读弱队赔率变动的反向信号）
        # 基本面悬殊
        big_gap_home = home_strong and away_weak
        big_gap_away = away_strong and home_weak
        # 赔率悬殊（弥补赛季胜率无法捕捉的极端强弱，如主1.1 vs 客16）
        odds_gap_home = odds_init_home < 1.5 and odds_init_away > 5.0
        odds_gap_away = odds_init_away < 1.5 and odds_init_home > 5.0
        big_gap_home = big_gap_home or odds_gap_home
        big_gap_away = big_gap_away or odds_gap_away

        # 综合意图指数：-1.0~1.0
        intent = 0.0
        # 阻盘(强队微升2-6%) → 庄家看好（+0.6）
        if home_strong and h_change == 1: intent += 0.6
        if away_strong and a_change == 1: intent -= 0.6
        # 真实看空(强队大涨>6%) → 庄家不看好（-0.8）
        if home_strong and h_change == 2: intent -= 0.8
        if away_strong and a_change == 2: intent += 0.8
        # 强队大幅降赔(>6%) → 市场极度看好（+0.8，比中等降赔更强）
        if home_strong and h_change == -2: intent += 0.8
        if away_strong and a_change == -2: intent -= 0.8
        # 强队中等降赔(2-6%) → 市场共识看好
        if home_strong and h_change == -1: intent += 0.3
        if away_strong and a_change == -1: intent -= 0.3
        # 诱盘(弱队降赔2-6%) → 庄家不看好弱队方向
        if home_weak and h_change == -1: intent -= 0.5
        if away_weak and a_change == -1: intent += 0.5
        # 诱盘(弱队大幅降赔>6%) → 更强诱盘信号
        if home_weak and h_change == -2: intent -= 0.7
        if away_weak and a_change == -2: intent += 0.7
        # 弱队中等升赔(2-6%) → 市场不看好弱队，确认对方优势
        if home_weak and h_change == 1: intent += 0.3
        if away_weak and a_change == 1: intent -= 0.3
        # 弱队大幅升赔(>6%) → 弱队被市场放弃
        if home_weak and h_change == 2: intent += 0.5
        if away_weak and a_change == 2: intent -= 0.5
        # 强弱悬殊 + 弱队赔率大幅降赔(>6%) → 庄家平衡盘口，实为确认强队
        if big_gap_home and a_change == -2: intent += 0.5
        if big_gap_away and h_change == -2: intent -= 0.5
        # 强弱悬殊 + 弱队赔率中等降赔(2-6%) → 同理，确认强队
        if big_gap_home and a_change == -1: intent += 0.3
        if big_gap_away and h_change == -1: intent -= 0.3
        features["bookmaker_intent"] = max(-1.0, min(1.0, intent))
        
        # V4.3: 赔率与基本面方向冲突检测
        # 当市场赔率方向与基本面统计方向矛盾时，降低意图信号置信度
        # 排他性判定：只有基本面明确偏向一方时才算有效方向
        stats_favor_home = (home_strong and not away_strong) or (away_weak and not home_weak)
        stats_favor_away = (away_strong and not home_strong) or (home_weak and not away_weak)
        odds_favor_home = odds_init_home < odds_init_away
        if odds_favor_home and stats_favor_away:
            features["bookmaker_intent"] *= 0.4
        elif (not odds_favor_home) and stats_favor_home:
            features["bookmaker_intent"] *= 0.4
        
        # V4.4: H2H xG 与赔率变动方向一致性检查
        # 核心逻辑：H2H xG反映真实交锋实力，当它与赔率变动方向矛盾时，赔率信号不可靠
        #   001例: H2H xG客队碾压(0.22:0.01) + 赔率转向客队 → 一致 → 信号可信
        #   003例: H2H xG主队稍优(1.35:1.15) + 赔率转向客队 → 矛盾 → 赔率信号不可靠，主队可能爆冷
        h2h_home_xg = features.get("h2h_avg_home_xg", 0) or 0
        h2h_away_xg = features.get("h2h_avg_away_xg", 0) or 0
        has_h2h = features.get("has_h2h", 0) or features.get("h2h_match_count", 0)
        h2h_favor_home = False
        h2h_favor_away = False
        odds_favor_home_move = home_change_pct < -0.02
        odds_favor_away_move = away_change_pct < -0.02
        if has_h2h and (h2h_home_xg > 0 or h2h_away_xg > 0):
            # 检测阈值0.02（贝叶斯平滑后差异更小，需要更敏感的阈值）
            h2h_favor_home = h2h_home_xg > h2h_away_xg + 0.02
            h2h_favor_away = h2h_away_xg > h2h_home_xg + 0.02
            
            if h2h_favor_home and odds_favor_away_move:
                # H2H xG看主队但赔率转向客队 → 赔率诱盘，削弱客队方向信号
                if features["bookmaker_intent"] < 0:
                    features["bookmaker_intent"] *= 0.3
            elif h2h_favor_away and odds_favor_home_move:
                # H2H xG看客队但赔率转向主队 → 赔率诱盘，削弱主队方向信号
                if features["bookmaker_intent"] > 0:
                    features["bookmaker_intent"] *= 0.3
        
        # V4.5: H2H xG-赔率一致性特征
        # +1 = H2H xG与赔率方向一致 → 强信号； -1 = 矛盾 → 赔率可能诱盘
        # 仅在有真实H2H xG数据时计算（默认值1.35/1.15不能反映真实交锋）
        h2h_dir = 0
        if h2h_favor_home: h2h_dir = 1
        elif h2h_favor_away: h2h_dir = -1
        
        odds_mv_dir = 0
        if odds_favor_home_move: odds_mv_dir = 1
        elif odds_favor_away_move: odds_mv_dir = -1
        
        # 检测H2H xG是否为真实数据（非默认先验值1.35/1.15）
        h2h_xg_real = h2h_home_xg != 1.35 or h2h_away_xg != 1.15
        if not h2h_xg_real:
            # 进一步检测：xG差异显著 ≠ 默认值差异
            raw_xg_diff = abs(h2h_home_xg - h2h_away_xg)
            h2h_xg_real = raw_xg_diff > 0.01 and not (abs(h2h_home_xg - 1.35) < 0.01 and abs(h2h_away_xg - 1.15) < 0.01)
        
        if h2h_dir != 0 and odds_mv_dir != 0 and h2h_xg_real:
            features["h2h_odds_alignment"] = 1.0 if h2h_dir == odds_mv_dir else -1.0
        else:
            features["h2h_odds_alignment"] = 0.0
        
        # 赔率+基本面背离度
        fundamental_advantage = home_ppg - away_ppg
        odds_advantage = -home_change_pct + away_change_pct  # 降赔=优势
        features["fundamental_odds_divergence"] = abs(fundamental_advantage + odds_advantage * 50)

    async def _get_match(self, match_id: int):
        result = await self.db.execute(select(Match).where(Match.id == match_id))
        return result.scalar_one_or_none()

    async def _get_rest_days(self, team_id: int, current_time) -> float:
        """查询球队上一场比赛距当前比赛的天数"""
        if not team_id or not current_time:
            return 7.0
        result = await self.db.execute(
            select(Match.kickoff_time).where(
                Match.home_team_id == team_id,
                Match.kickoff_time < current_time
            ).union(
                select(Match.kickoff_time).where(
                    Match.away_team_id == team_id,
                    Match.kickoff_time < current_time
                )
            ).order_by(Match.kickoff_time.desc()).limit(1)
        )
        row = result.first()
        if not row or not row[0]:
            return 7.0
        last_time = row[0]
        if hasattr(last_time, 'tzinfo') and last_time.tzinfo is not None:
            last_time = last_time.replace(tzinfo=None)
        delta = current_time - last_time
        return max(0.0, delta.total_seconds() / 86400.0)

    async def _get_team_stats(self, team_id: int):
        if not team_id:
            return None
        result = await self.db.execute(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team_id
            ).order_by(TeamSeasonStats.id.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_h2h(self, team1_id: int, team2_id: int, before_date=None) -> list:
        """获取历史交锋记录，排除比赛日当天已完赛的场次"""
        if not team1_id or not team2_id:
            return []
        from sqlalchemy import func as sa_func
        from datetime import timedelta
        query = select(HeadToHead).where(
            ((HeadToHead.home_team_id == team1_id) & (HeadToHead.away_team_id == team2_id)) |
            ((HeadToHead.home_team_id == team2_id) & (HeadToHead.away_team_id == team1_id))
        )
        if before_date:
            from datetime import timedelta, date as dt_date
            utc_date = before_date - timedelta(hours=8) if hasattr(before_date, 'strftime') else before_date
            cutoff_date = utc_date.date() if hasattr(utc_date, 'date') else dt_date.fromisoformat(str(utc_date)[:10])
            query = query.where(sa_func.date(HeadToHead.match_date) < cutoff_date)
        query = query.order_by(HeadToHead.match_date.desc()).limit(10)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def _get_odds_structured(self, match_id: int) -> dict:
        """V4: 按博彩公司分组获取结构化赔率数据

        返回 dict:
          - has_data: bool
          - by_bookmaker: {bookmaker_name: [OddsSnapshot]}  按时间升序
          - times: 去重排序的时间点列表
          - latest: 最新时间点的所有博彩公司快照
          - prev: 次新时间点的所有博彩公司快照（无则为 []）
          - bookmaker_count: 博彩公司数量
          - time_count: 时间点数量
        """
        result = await self.db.execute(
            select(OddsSnapshot).where(
                OddsSnapshot.match_id == match_id
            ).order_by(OddsSnapshot.snapshot_time.asc())
        )
        all_odds = list(result.scalars().all())

        if not all_odds:
            return {"has_data": False}

        # 按博彩公司分组（时间升序）
        by_bookmaker: dict[str, list] = {}
        for o in all_odds:
            bm = o.bookmaker or "unknown"
            by_bookmaker.setdefault(bm, []).append(o)

        # 去重时间点
        times = sorted(set(o.snapshot_time for o in all_odds))

        # 最新 / 次新时间点的快照
        latest_time = times[-1]
        latest = [o for o in all_odds if o.snapshot_time == latest_time]
        prev = []
        if len(times) >= 2:
            prev_time = times[-2]
            prev = [o for o in all_odds if o.snapshot_time == prev_time]

        return {
            "has_data": True,
            "by_bookmaker": by_bookmaker,
            "times": times,
            "latest": latest,
            "prev": prev,
            "bookmaker_count": len(by_bookmaker),
            "time_count": len(times),
        }

    async def _get_injury_count(self, team_id: int) -> int:
        if not team_id:
            return 0
        result = await self.db.execute(
            select(Injury).where(Injury.team_id == team_id, Injury.status == "out")
        )
        return len(list(result.scalars().all()))

    async def _get_league_baseline(self, league_id: int) -> dict:
        """V4: 计算联赛基线统计（带缓存），用于跨联赛特征归一化"""
        # 全局默认基线：联赛不可用时用典型值代替，避免 StandardScaler 将 0 映射为极端负值
        FALLBACK_BASELINE = {
            "league_count": 0,
            "league_avg_home_goals": 1.5,
            "league_avg_away_goals": 1.2,
            "league_home_win_rate": 0.45,
            "league_draw_rate": 0.25,
            "league_avg_total_goals": 2.7,
        }
        if not league_id:
            return FALLBACK_BASELINE
        if league_id in FeatureEngineer._league_baselines:
            return FeatureEngineer._league_baselines[league_id]

        # 查询该联赛所有已完成比赛
        result = await self.db.execute(
            select(
                func.count(Match.id),
                func.avg(Match.home_score),
                func.avg(Match.away_score),
                func.avg(case((Match.home_score > Match.away_score, 1), else_=0)),
                func.avg(case((Match.home_score == Match.away_score, 1), else_=0)),
            ).where(
                Match.league_id == league_id,
                Match.home_score.isnot(None),
                Match.status == "finished",
            )
        )
        row = result.one()
        count = row[0] or 0
        if count < 10:
            baseline = dict(FALLBACK_BASELINE)  # 数据太少，用全局默认值
        else:
            baseline = {
                "league_count": count,
                "league_avg_home_goals": float(row[1] or 0),
                "league_avg_away_goals": float(row[2] or 0),
                "league_home_win_rate": float(row[3] or 0),
                "league_draw_rate": float(row[4] or 0),
                "league_avg_total_goals": float((row[1] or 0) + (row[2] or 0)),
            }

        FeatureEngineer._league_baselines[league_id] = baseline
        return baseline

    @staticmethod
    def _normalize_by_league(features: dict, baseline: dict, home_stats, away_stats) -> dict:
        """V4: 将球队原始统计除以联赛均值，实现跨联赛可比"""
        if not baseline:
            return {}

        feats = {}
        lahg = baseline.get("league_avg_home_goals", 1.5) or 1.5
        laag = baseline.get("league_avg_away_goals", 1.2) or 1.2
        lhw = baseline.get("league_home_win_rate", 0.45) or 0.45
        ldw = baseline.get("league_draw_rate", 0.25) or 0.25
        ltg = baseline.get("league_avg_total_goals", 2.7) or 2.7

        # 主队归一化：球队值 / 联赛均值（>1 表示高于联赛平均）
        feats["home_win_rate_norm"] = features.get("home_win_rate", 0) / max(lhw, 0.01)
        feats["home_goals_avg_norm"] = features.get("home_goals_avg", 0) / max(lahg, 0.01)
        feats["home_goals_against_avg_norm"] = features.get("home_goals_against_avg", 0) / max(laag, 0.01)

        # 客队归一化
        away_win_rate = features.get("away_win_rate", 0)
        feats["away_win_rate_norm"] = away_win_rate / max(1 - lhw - ldw, 0.01)  # 客胜率相对联赛客胜基线
        feats["away_goals_avg_norm"] = features.get("away_goals_avg", 0) / max(laag, 0.01)
        feats["away_goals_against_avg_norm"] = features.get("away_goals_against_avg", 0) / max(lahg, 0.01)

        # xG 归一化（用联赛场均进球作为代理基线，xG 无联赛级汇总）
        feats["home_xg_norm"] = features.get("home_xG", 0) / max(ltg / 2, 0.01)
        feats["away_xg_norm"] = features.get("away_xG", 0) / max(ltg / 2, 0.01)

        # 联赛场均进球（作为比赛节奏信号）
        feats["league_avg_total_goals"] = ltg
        feats["league_home_win_rate"] = lhw

        # 球队风格特征（从已有数据派生）
        if home_stats and home_stats.avg_possession:
            feats["home_possession_tendency"] = float(home_stats.avg_possession) / 50.0  # 归一化到 ~1.0
        else:
            feats["home_possession_tendency"] = 1.0

        if away_stats and away_stats.avg_possession:
            feats["away_possession_tendency"] = float(away_stats.avg_possession) / 50.0
        else:
            feats["away_possession_tendency"] = 1.0

        # 风格冲突度
        feats["style_clash_possession"] = abs(
            feats["home_possession_tendency"] - feats["away_possession_tendency"]
        )

        # 进攻效率（实际进球/预期进球，>1 说明把握机会能力强）
        home_xg = features.get("home_xG", 0) or 0.01
        away_xg = features.get("away_xG", 0) or 0.01
        feats["home_attacking_efficiency"] = features.get("home_goals_avg", 0) / max(home_xg, 0.1)
        feats["away_attacking_efficiency"] = features.get("away_goals_avg", 0) / max(away_xg, 0.1)

        # 防守强度（xGA 越低越好，>1=防守差于平均）
        feats["home_defensive_index"] = features.get("home_goals_against_avg", 0) / max(features.get("home_xGA", 1), 0.1)
        feats["away_defensive_index"] = features.get("away_goals_against_avg", 0) / max(features.get("away_xGA", 1), 0.1)

        return feats

    # ── 特征提取方法 ──

    def _extract_team_strength(self, home, away, baseline: dict = None) -> dict:
        """类别 A: 球队基础战力特征（约 22 维）
        
        V4.1: 球队数据缺失时用联赛均值 fallback，避免填 0 导致主客不对称
        """
        if baseline is None:
            baseline = {}
        feats = {}

        # ── 主队特征 ──
        if home and home.played and home.played > 0:
            p = home.played
            feats["home_win_rate"] = home.wins / p
            feats["home_draw_rate"] = home.draws / p
            feats["home_goals_avg"] = home.goals_for / p
            feats["home_goals_against_avg"] = home.goals_against / p
            feats["home_home_win_rate"] = home.home_wins / max(home.home_wins + home.home_draws + home.home_losses, 1)
            feats["home_clean_sheet_rate"] = home.clean_sheets / max(p, 1)
            feats["home_xG"] = home.xG or 0
            feats["home_xGA"] = home.xGA or 0
            home_pts = home.wins * 3 + home.draws
            feats["home_points_per_game"] = home_pts / p
        else:
            feats["home_win_rate"] = baseline.get("league_home_win_rate", 0.45)
            feats["home_draw_rate"] = baseline.get("league_draw_rate", 0.25)
            feats["home_goals_avg"] = baseline.get("league_avg_home_goals", 1.5)
            feats["home_goals_against_avg"] = baseline.get("league_avg_away_goals", 1.2)
            feats["home_home_win_rate"] = baseline.get("league_home_win_rate", 0.45)
            feats["home_clean_sheet_rate"] = 0.2
            feats["home_xG"] = 0.0
            feats["home_xGA"] = 0.0
            feats["home_points_per_game"] = baseline.get("league_home_win_rate", 0.45) * 3 + baseline.get("league_draw_rate", 0.25)

        # ── 客队特征 ──
        if away and away.played and away.played > 0:
            p = away.played
            feats["away_win_rate"] = away.wins / p
            feats["away_goals_avg"] = away.goals_for / p
            feats["away_goals_against_avg"] = away.goals_against / p
            feats["away_away_win_rate"] = away.away_wins / max(away.away_wins + away.away_draws + away.away_losses, 1)
            feats["away_xG"] = away.xG or 0
            feats["away_xGA"] = away.xGA or 0
            away_pts = away.wins * 3 + away.draws
            feats["away_points_per_game"] = away_pts / p
        else:
            feats["away_win_rate"] = 1.0 - baseline.get("league_home_win_rate", 0.45) - baseline.get("league_draw_rate", 0.25)
            feats["away_goals_avg"] = baseline.get("league_avg_away_goals", 1.2)
            feats["away_goals_against_avg"] = baseline.get("league_avg_home_goals", 1.5)
            feats["away_away_win_rate"] = 1.0 - baseline.get("league_home_win_rate", 0.45) - baseline.get("league_draw_rate", 0.25)
            feats["away_xG"] = 0.0
            feats["away_xGA"] = 0.0
            feats["away_points_per_game"] = (1.0 - baseline.get("league_home_win_rate", 0.45) - baseline.get("league_draw_rate", 0.25)) * 3 + baseline.get("league_draw_rate", 0.25)

        # ── 差值特征 ──
        feats["win_rate_diff"] = feats.get("home_win_rate", 0) - feats.get("away_win_rate", 0)
        feats["goals_avg_diff"] = feats.get("home_goals_avg", 0) - feats.get("away_goals_against_avg", 0)
        feats["xG_diff"] = feats.get("home_xG", 0) - feats.get("away_xGA", 0)
        feats["points_per_game_diff"] = feats.get("home_points_per_game", 0) - feats.get("away_points_per_game", 0)

        # V4.1: 对阵强度特征（区分强强对话 vs 强弱悬殊 vs 弱弱保级）
        hwr = max(feats.get("home_win_rate", 0), 0.01)
        awr = max(feats.get("away_win_rate", 0), 0.01)
        feats["match_intensity"] = (hwr * awr) ** 0.5  # 几何平均：两强相遇→高，双弱→低
        total_strength = hwr + awr
        feats["strength_asymmetry"] = abs(hwr - awr) / total_strength  # 0=势均力敌, 1=一边倒

        return feats

    def _extract_contextual(self, match, home_stats, away_stats) -> dict:
        """类别 A+: 上下文特征（V2 新增，约 8 维）"""
        feats = {}

        # 联赛 ID（让 LightGBM 学习联赛级差异）
        feats["league_id"] = float(match.league_id) if match.league_id else 0.0

        # 赛季阶段：0=初期(8-9月), 1=中期(10-1月), 2=末期(2-7月)
        if match.kickoff_time:
            month = match.kickoff_time.month
            feats["season_stage"] = 0.0 if month in [8, 9] else 1.0 if month in [10, 11, 12, 1] else 2.0
            # 是否为周末比赛（周末关注度/投注量不同，赔率可能更准确）
            weekday = match.kickoff_time.weekday()
            feats["is_weekend"] = 1.0 if weekday >= 5 else 0.0
        else:
            feats["season_stage"] = 1.0
            feats["is_weekend"] = 0.0

        # 已赛场次（赛季进度代理，反映球队疲劳度和战术成熟度）
        feats["home_games_played"] = float(home_stats.played) if home_stats else 0.0
        feats["away_games_played"] = float(away_stats.played) if away_stats else 0.0

        # V4.1: 赛季进度比（已赛场次占赛季总场次比例，反映磨合程度）
        # 默认 38 场赛季，最小 10 场
        season_total = max(feats["home_games_played"] + feats.get("home_win_rate", 0) * 0 + 38, 10)
        feats["season_progress_ratio"] = min(
            ((feats["home_games_played"] + feats["away_games_played"]) / 2) / season_total, 1.0
        )

        # 积分差（实力差距的直接度量）
        if home_stats and away_stats:
            home_pts = home_stats.wins * 3 + home_stats.draws
            away_pts = away_stats.wins * 3 + away_stats.draws
            feats["points_diff"] = float(home_pts - away_pts)
        else:
            feats["points_diff"] = 0.0

        # 让球线（竞彩开盘的核心参数，直接作为特征）
        feats["handicap_line"] = float(match.handicap_line) if match.handicap_line else 0.0

        # ── 球队动机压力（保级/夺冠/无欲无求） ──
        ss = feats.get("season_stage", 1.0)
        stage_factor = 0.3 if ss <= 0.5 else 0.7 if ss <= 1.5 else 1.0
        feats["home_motivation"] = self._calc_motivation(home_stats, stage_factor)
        feats["away_motivation"] = self._calc_motivation(away_stats, stage_factor)
        feats["motivation_diff"] = feats["home_motivation"] - feats["away_motivation"]

        return feats

    @staticmethod
    def _calc_motivation(stats, stage_factor: float, total_games: int = 38) -> float:
        """
        计算球队动机压力（0~1）。
        
        逻辑：
        - ppg ≥ 2.0（争冠组）：越接近赛季末战意越强 → 0.6~1.0
        - ppg 1.6~2.0（欧战区）：中等战意 → 0.4~0.7
        - ppg 1.0~1.6（中游）：战意随赛季衰减 → 随进度从0.5降到0.1
        - ppg < 1.0（保级区）：越接近赛季末恐惧越大 → 0.5~1.0
        
        最后乘以赛季阶段因子（初期0.3/中期0.7/末期1.0）。
        """
        if not stats or not stats.played or stats.played == 0:
            return 0.3 * stage_factor

        ppg = (stats.wins * 3 + stats.draws) / stats.played
        progress = min(stats.played / total_games, 1.0)

        if ppg >= 2.0:
            raw = 0.6 + 0.4 * progress       # 夺冠驱动: 越后越强
        elif ppg >= 1.6:
            raw = 0.4 + 0.3 * progress       # 欧战驱动: 中等上升
        elif ppg >= 1.0:
            raw = max(0.1, 0.5 - 0.4 * progress)  # 中游衰减: 越后越无欲
        else:
            raw = 0.5 + 0.5 * progress       # 保级恐惧: 越后越拼命

        return round(raw * stage_factor, 4)

    def _extract_h2h(self, h2h_list: list, home_team_id: int, away_team_id: int) -> dict:
        """类别 B: 交锋记录特征（V4.11：stats 缺失时回退比分数据）

        优先使用 xG、射门、控球、危险进攻等过程数据；
        当 stats JSON 为空时（SportMonks stats 未采集），回退到比分数据
        （进球数、胜负率），确保 H2H 信号在任何情况下都有数据支撑。
        """
        has_h2h = len(h2h_list) >= 1
        feats = {
            "has_h2h": 1.0 if has_h2h else 0.0,
            "h2h_match_count": len(h2h_list),
            # 进攻内容特征（默认值 = 全球均值）
            "h2h_avg_home_xg": 1.35,
            "h2h_avg_away_xg": 1.15,
            "h2h_avg_xg_diff": 0.2,
            "h2h_avg_home_shots": 13.0,
            "h2h_avg_away_shots": 11.0,
            "h2h_avg_shots_ratio": 0.55,
            "h2h_avg_home_possession": 50.0,
            "h2h_avg_home_dangerous": 45.0,
            "h2h_avg_away_dangerous": 40.0,
            # 比分特征（stats 缺失时的 fallback）
            "h2h_avg_home_goals": 1.5,
            "h2h_avg_away_goals": 1.3,
            "h2h_home_win_rate": 0.33,
            "h2h_away_win_rate": 0.33,
            "h2h_draw_rate": 0.34,
            # 标记 stats 是否真实（非默认值）
            "h2h_stats_available": 0.0,
        }

        # 收集进攻内容数据
        home_xgs, away_xgs = [], []
        home_shots, away_shots = [], []
        home_poss, home_dang, away_dang = [], [], []
        # 收集比分数据（始终收集）
        home_goals, away_goals = [], []
        home_wins, away_wins, draws = 0, 0, 0

        for h in h2h_list:
            if h.home_score is None or h.away_score is None:
                continue

            # 方向对齐
            if h.home_team_id == home_team_id:
                h_g, a_g = h.home_score, h.away_score
            else:
                h_g, a_g = h.away_score, h.home_score

            # 始终收集比分
            home_goals.append(h_g)
            away_goals.append(a_g)
            if h_g > a_g:
                home_wins += 1
            elif h_g < a_g:
                away_wins += 1
            else:
                draws += 1

            # 尝试收集 stats（可能为空）
            hs = h.home_stats or {}
            aws = h.away_stats or {}
            if not hs and not aws:
                continue  # stats 为空则只收集比分

            # 方向对齐 stats
            if h.home_team_id == home_team_id:
                h_stats, a_stats = hs, aws
            else:
                h_stats, a_stats = aws, hs

            # xG
            hx = h_stats.get("xG")
            ax = a_stats.get("xG")
            if hx is not None and ax is not None:
                home_xgs.append(float(hx))
                away_xgs.append(float(ax))

            # 射门
            hsht = h_stats.get("shots")
            asht = a_stats.get("shots")
            if hsht is not None and asht is not None:
                home_shots.append(float(hsht))
                away_shots.append(float(asht))

            # 控球
            hpos = h_stats.get("possession")
            if hpos is not None:
                home_poss.append(float(hpos))

            # 危险进攻
            hd = h_stats.get("dangerous")
            ad = a_stats.get("dangerous")
            if hd is not None:
                home_dang.append(float(hd))
            if ad is not None:
                away_dang.append(float(ad))

        total_matches = home_wins + away_wins + draws
        if total_matches > 0:
            feats["h2h_home_win_rate"] = round(home_wins / total_matches, 4)
            feats["h2h_away_win_rate"] = round(away_wins / total_matches, 4)
            feats["h2h_draw_rate"] = round(draws / total_matches, 4)

        if home_goals:
            feats["h2h_avg_home_goals"] = round(sum(home_goals) / len(home_goals), 2)
            feats["h2h_avg_away_goals"] = round(sum(away_goals) / len(away_goals), 2)

        # 用贝叶斯平滑聚合 stats（先验强度 = 2场虚拟数据）
        ps = 2.0

        def _smooth_mean(vals, prior_mean, prior_strength=ps):
            if not vals:
                return prior_mean
            ps_val = prior_strength if isinstance(prior_strength, (int, float)) else 2.0
            return (sum(vals) + prior_mean * ps_val) / (len(vals) + ps_val)

        if home_xgs:
            feats["h2h_avg_home_xg"] = _smooth_mean(home_xgs, 1.35, prior_strength=0.5)
            feats["h2h_avg_away_xg"] = _smooth_mean(away_xgs, 1.15, prior_strength=0.5)
            feats["h2h_avg_xg_diff"] = feats["h2h_avg_home_xg"] - feats["h2h_avg_away_xg"]
            feats["h2h_stats_available"] = 1.0

        if home_shots:
            feats["h2h_avg_home_shots"] = _smooth_mean(home_shots, 13.0)
            feats["h2h_avg_away_shots"] = _smooth_mean(away_shots, 11.0)
            total = feats["h2h_avg_home_shots"] + feats["h2h_avg_away_shots"]
            feats["h2h_avg_shots_ratio"] = feats["h2h_avg_home_shots"] / max(total, 0.1)

        if home_poss:
            raw_poss = _smooth_mean(home_poss, 50.0)
            # 方差压缩：42% vs 52% 控球 → 势均力敌，贡献差异仅 ~1.21x
            # 以 50% 为中心，压缩到 20% 方差，保持模型校准不被破坏
            POSS_CENTER = 50.0
            POSS_COMPRESSION = 0.2
            feats["h2h_avg_home_possession"] = round(
                POSS_CENTER + (raw_poss - POSS_CENTER) * POSS_COMPRESSION, 1
            )

        feats["h2h_avg_home_dangerous"] = _smooth_mean(home_dang, 45.0)
        feats["h2h_avg_away_dangerous"] = _smooth_mean(away_dang, 40.0)

        return feats

    def _extract_recent_form(self, home_stats, away_stats, features: dict = None, before_date=None) -> dict:
        """类别 E: 近期状态特征（V3 新增，约 14 维）
        
        从 recent_matches JSON 提取最近比赛表现，反映球队当前状态而非全赛季平均。
        V4.1: 赛季初期（已赛场次少）加大贝叶斯收缩力度。
        V4.12: before_date 过滤比赛日当天比赛，防止数据泄露。
        """
        if features is None:
            features = {}
        feats = {}

        # 将 before_date（北京时间）转为 UTC 日期字符串用于过滤
        cutoff_date = None
        if before_date:
            from datetime import timedelta
            utc_dt = before_date - timedelta(hours=8) if hasattr(before_date, 'strftime') else before_date
            cutoff_date = utc_dt.strftime("%Y-%m-%d") if hasattr(utc_dt, 'strftime') else str(utc_dt)[:10]

        def _parse_team_form(stats, prefix: str):
            """解析单队近期状态，返回特征 dict"""
            matches = stats.recent_matches if stats and stats.recent_matches else []
            if not isinstance(matches, list) or len(matches) == 0:
                for k in [f"{prefix}_form_pts_6", f"{prefix}_form_pts_10",
                          f"{prefix}_gf_avg_6", f"{prefix}_ga_avg_6",
                          f"{prefix}_form_trend", f"{prefix}_home_form_pts",
                          f"{prefix}_away_form_pts"]:
                    feats[k] = 0.0
                return

            # 解析每场比赛：W/D/L → 积分，进球/失球
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

                pts = 3 if result == "W" else 1 if result == "D" else 0
                gf, ga = self._parse_recent_score(score)

                parsed.append({"pts": pts, "gf": gf, "ga": ga, "is_home": is_home})

            if not parsed:
                for k in [f"{prefix}_form_pts_6", f"{prefix}_form_pts_10",
                          f"{prefix}_gf_avg_6", f"{prefix}_ga_avg_6",
                          f"{prefix}_form_trend", f"{prefix}_home_form_pts",
                          f"{prefix}_away_form_pts"]:
                    feats[k] = 0.0
                return

            n = len(parsed)
            # 最近 6 场（不足 6 场则用全部）
            window6 = parsed[:min(6, n)]
            window10 = parsed[:min(10, n)]

            feats[f"{prefix}_form_pts_6"] = sum(m["pts"] for m in window6) / len(window6)  # 场均积分
            feats[f"{prefix}_form_pts_10"] = sum(m["pts"] for m in window10) / len(window10)

            # 场均进球/失球（方案B: 裁剪 + 方案D: 小样本贝叶斯收缩）
            n6 = len(window6)
            raw_gf = sum(m["gf"] for m in window6) / n6
            raw_ga = sum(m["ga"] for m in window6) / n6

            # 方案B: Winsorize 上限 3.0（单场场均进球极少超3球）
            raw_gf = min(raw_gf, 3.0)
            raw_ga = min(raw_ga, 3.0)

            # 方案D: 小样本贝叶斯收缩，向联赛均值先验收缩
            # 先验取 1.4 球（联赛场均总进球 ~2.8 / 2 队）
            GF_PRIOR = 1.4
            GA_PRIOR = 1.4
            # V4.1: 赛季初期加大收缩力度
            # 已赛场次少 → prior_strength 大 → 更依赖先验
            games_played = features.get(f"{prefix}_games_played", 10)
            base_strength = 5.0 + max(0, 10 - games_played)  # 0场→15, 10场→5, 20场→5
            prior_strength = min(base_strength, 15.0)
            shrinkage = prior_strength / (prior_strength + n6)
            feats[f"{prefix}_gf_avg_6"] = round(shrinkage * GF_PRIOR + (1 - shrinkage) * raw_gf, 4)
            feats[f"{prefix}_ga_avg_6"] = round(shrinkage * GA_PRIOR + (1 - shrinkage) * raw_ga, 4)

            # 状态趋势：后一半 - 前一半
            if n >= 4:
                mid = n // 2
                recent_half = parsed[:mid]
                older_half = parsed[mid:mid * 2]
                feats[f"{prefix}_form_trend"] = (
                    sum(m["pts"] for m in recent_half) / max(len(recent_half), 1) -
                    sum(m["pts"] for m in older_half) / max(len(older_half), 1)
                )
            else:
                feats[f"{prefix}_form_trend"] = 0.0

            # 主/客场近况（最近6场中的主/客场比赛）
            home_matches = [m for m in window6 if m["is_home"]]
            away_matches = [m for m in window6 if not m["is_home"]]
            feats[f"{prefix}_home_form_pts"] = (
                sum(m["pts"] for m in home_matches) / max(len(home_matches), 1)
                if home_matches else 0.0
            )
            feats[f"{prefix}_away_form_pts"] = (
                sum(m["pts"] for m in away_matches) / max(len(away_matches), 1)
                if away_matches else 0.0
            )

        _parse_team_form(home_stats, "home")
        _parse_team_form(away_stats, "away")
        return feats

    # ── V4 赔率特征（重构：按博彩公司分组真实时序 + 亚盘/大小球/共识度）──

    @staticmethod
    def _safe_mean(vals: list) -> float:
        """安全求均值，空列表返回 0"""
        vals = [v for v in vals if v is not None]
        return float(np.mean(vals)) if vals else 0.0

    @staticmethod
    def _safe_std(vals: list) -> float:
        """安全求标准差"""
        vals = [v for v in vals if v is not None]
        return float(np.std(vals)) if len(vals) >= 2 else 0.0

    def _extract_odds(self, odds_data: dict) -> dict:
        """类别 C: 赔率信号特征 V4

        包含四个维度：
          1) 1X2 欧赔（多博彩公司均值 + 真实时序变动 + 离散度）
          2) 亚盘（水位 + 让球线 + 与 JC 差异）
          3) 大小球（盘口 + 水位）
          4) 共识度（方向一致性 + 分歧趋势）

        共约 44 维赔率特征。
        """
        # ── 默认值 ──
        feats = {
            # 1X2 基础
            "odds_home_current": 0.0, "odds_draw_current": 0.0, "odds_away_current": 0.0,
            "odds_home_initial": 0.0, "odds_draw_initial": 0.0, "odds_away_initial": 0.0,
            "odds_movement_home": 0.0, "odds_movement_draw": 0.0, "odds_movement_away": 0.0,
            "odds_change_pct_home": 0.0, "odds_change_pct_draw": 0.0, "odds_change_pct_away": 0.0,
            "odds_implied_home_change": 0.0, "draw_odds_current": 0.0,
            "odds_std_home": 0.0, "odds_std_draw": 0.0, "odds_std_away": 0.0,
            "odds_dispersity": 0.0,
            "odds_market_home_prob": 1/3, "odds_market_draw_prob": 1/3, "odds_market_away_prob": 1/3,
            # 亚盘
            "handicap_home_current": 0.0, "handicap_away_current": 0.0,
            "handicap_line_market": 0.0, "handicap_line_jc_diff": 0.0,
            "handicap_home_movement": 0.0, "handicap_away_movement": 0.0,
            "handicap_line_shift": 0.0,
            "handicap_implied_home_prob": 0.0, "handicap_implied_away_prob": 0.0,
            # 大小球
            "goal_line_market": 0.0, "goal_line_jc_diff": 0.0,
            "over_odds_current": 0.0, "under_odds_current": 0.0,
            "over_odds_movement": 0.0, "under_odds_movement": 0.0,
            "goal_line_change": 0.0,
            # V4.6 大小球盘口趋势特征（捕获市场方向性信号）
            "goal_line_max": 0.0, "goal_line_min": 0.0,
            "goal_line_drop_from_peak": 0.0,  # 盘口从最高点回落幅度（正值=市场在降盘，看小球）
            "over_odds_decline_rate": 0.0,     # 大球水位小时级下降速率（正值=水位在跌）
            "goal_line_volatility": 0.0,        # 盘口波动性（跨博彩公司标准差）
            # 共识度
            "odds_consensus_direction": 0.0, "handicap_consensus_direction": 0.0,
            "odds_divergence_trend": 0.0, "handicap_divergence_trend": 0.0,
            # 元信息
            "bookmaker_count": 0, "odds_time_depth": 14.0,
        }

        if not odds_data.get("has_data"):
            return feats

        by_bm = odds_data["by_bookmaker"]
        latest = odds_data["latest"]
        prev_snaps = odds_data["prev"]
        times = odds_data["times"]
        all_odds_list = [o for snaps in by_bm.values() for o in snaps]
        feats["bookmaker_count"] = odds_data["bookmaker_count"]
        # odds_time_depth = 赔率采集轮次，纯数据管道产物，与进球无因果关系
        # 固定为训练数据均值 14.0，等同从模型中移除此特征
        feats["odds_time_depth"] = 14.0

        # ═══════════════════════════════════════════════
        # 维度 1: 1X2 欧赔特征
        # ═══════════════════════════════════════════════

        # 1.1 当前赔率：最新时间点所有博彩公司均值
        if latest:
            feats["odds_home_current"] = self._safe_mean([o.home_win for o in latest])
            feats["odds_draw_current"] = self._safe_mean([o.draw for o in latest])
            feats["odds_away_current"] = self._safe_mean([o.away_win for o in latest])
            feats["draw_odds_current"] = feats["odds_draw_current"]

        # 1.2 离散度：同一时间点各博彩公司的标准差
        if len(latest) >= 2:
            feats["odds_std_home"] = self._safe_std([o.home_win for o in latest])
            feats["odds_std_draw"] = self._safe_std([o.draw for o in latest])
            feats["odds_std_away"] = self._safe_std([o.away_win for o in latest])
            feats["odds_dispersity"] = max(feats["odds_std_home"], feats["odds_std_draw"], feats["odds_std_away"])

        # 1.3 真实时序变动：按博彩公司配对初盘→即时盘，取均值
        movements_h = []
        movements_d = []
        movements_a = []
        pct_h = []
        pct_d = []
        pct_a = []
        initial_h = []
        initial_d = []
        initial_a = []

        for bm, snaps in by_bm.items():
            if len(snaps) < 1:
                continue
            first = snaps[0]
            last = snaps[-1]

            initial_h.append(first.home_win)
            initial_d.append(first.draw)
            initial_a.append(first.away_win)

            if len(snaps) >= 2:
                fh = first.home_win or 0
                lh = last.home_win or 0
                fd = first.draw or 0
                ld = last.draw or 0
                fa = first.away_win or 0
                la = last.away_win or 0

                movements_h.append(fh - lh)  # 正=赔率下降=更被看好
                movements_d.append(fd - ld)
                movements_a.append(fa - la)

                if fh > 0:
                    pct_h.append((fh - lh) / fh)
                if fd > 0:
                    pct_d.append((fd - ld) / fd)
                if fa > 0:
                    pct_a.append((fa - la) / fa)
            else:
                # 单时间点：变动为 0
                movements_h.append(0.0)
                movements_d.append(0.0)
                movements_a.append(0.0)
                pct_h.append(0.0)
                pct_d.append(0.0)
                pct_a.append(0.0)

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

            # 隐含概率变化
            hi = 1.0 / max(feats["odds_home_initial"], 0.01)
            hc = 1.0 / max(feats["odds_home_current"], 0.01) if feats["odds_home_current"] > 0 else 0
            feats["odds_implied_home_change"] = hc - hi

        # 1.4 分歧趋势：最新 vs 次新时间点的标准差变化
        if prev_snaps and len(prev_snaps) >= 2 and len(latest) >= 2:
            prev_std = self._safe_std([o.home_win for o in prev_snaps])
            cur_std = feats["odds_std_home"]
            feats["odds_divergence_trend"] = cur_std - prev_std  # 正=分歧在扩大

        # 1.5 市场隐含概率（1/赔率归一化）
        h, d, a = feats["odds_home_current"], feats["odds_draw_current"], feats["odds_away_current"]
        if h > 0 and d > 0 and a > 0:
            imp_h = 1.0 / h
            imp_d = 1.0 / d
            imp_a = 1.0 / a
            total = imp_h + imp_d + imp_a
            if total > 0:
                feats["odds_market_home_prob"] = imp_h / total
                feats["odds_market_draw_prob"] = imp_d / total
                feats["odds_market_away_prob"] = imp_a / total

        # ═══════════════════════════════════════════════
        # 维度 2: 亚盘特征（按最共识盘口线分组，避免混合不同盘口）
        # ═══════════════════════════════════════════════

        # 2.1 找到市场共识盘口线：水位最接近均衡（|home_odds - away_odds| 最小）的盘口
        best_line = None
        best_balance = float("inf")
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

        # 2.2 仅使用共识盘口线上的赔率
        hcp_at_line = [o for o in latest
                       if o.handicap_line == consensus_line
                       and o.handicap_home is not None and o.handicap_away is not None]
        if hcp_at_line:
            feats["handicap_line_market"] = float(consensus_line)
            feats["handicap_home_current"] = self._safe_mean([o.handicap_home for o in hcp_at_line])
            feats["handicap_away_current"] = self._safe_mean([o.handicap_away for o in hcp_at_line])

            # 亚盘隐含概率（水位越低→隐含概率越高）
            hh = feats["handicap_home_current"]
            ha = feats["handicap_away_current"]
            if hh > 0 and ha > 0:
                imp_hh = 1.0 / hh
                imp_ha = 1.0 / ha
                total_hcp = imp_hh + imp_ha
                if total_hcp > 0:
                    feats["handicap_implied_home_prob"] = imp_hh / total_hcp
                    feats["handicap_implied_away_prob"] = imp_ha / total_hcp

        # 2.3 亚盘时序变动：仅追踪相同盘口线在各博彩公司间的变动
        hcp_move_h = []
        hcp_move_a = []
        hcp_line_shift = []
        for bm, snaps in by_bm.items():
            hcp_snaps = [s for s in snaps if s.handicap_home is not None and s.handicap_line == consensus_line]
            if len(hcp_snaps) >= 2:
                f = hcp_snaps[0]
                l = hcp_snaps[-1]
                hcp_move_h.append((f.handicap_home or 0) - (l.handicap_home or 0))
                hcp_move_a.append((f.handicap_away or 0) - (l.handicap_away or 0))
                hcp_line_shift.append((l.handicap_line or 0) - (f.handicap_line or 0))
            elif len(hcp_snaps) == 1:
                hcp_move_h.append(0.0)
                hcp_move_a.append(0.0)
                hcp_line_shift.append(0.0)

        if hcp_move_h:
            feats["handicap_home_movement"] = self._safe_mean(hcp_move_h)
            feats["handicap_away_movement"] = self._safe_mean(hcp_move_a)
            feats["handicap_line_shift"] = self._safe_mean(hcp_line_shift)

        # 2.4 亚盘分歧趋势：相同盘口线在各博彩公司间标准差的时序变化
        if prev_snaps and consensus_line is not None:
            prev_hcp = [o for o in prev_snaps
                        if o.handicap_line == consensus_line and o.handicap_home is not None]
            cur_hcp = hcp_at_line
            if len(prev_hcp) >= 2 and len(cur_hcp) >= 2:
                prev_hcp_std = self._safe_std([o.handicap_home for o in prev_hcp])
                cur_hcp_std = self._safe_std([o.handicap_home for o in cur_hcp])
                feats["handicap_divergence_trend"] = cur_hcp_std - prev_hcp_std

        # ═══════════════════════════════════════════════
        # 维度 3: 大小球特征（V4.6 重构：支持部分数据 + 趋势信号）
        # ═══════════════════════════════════════════════

        # 3.1 收集所有时间点的大小球数据（不要求 over/under 同时存在）
        all_ou_times = []  # [(snapshot_time, goal_line, over_odds, under_odds), ...]
        for t in times:
            snaps_at_t = [o for o in all_odds_list if o.snapshot_time == t]
            for o in snaps_at_t:
                if o.goal_line is not None and (o.over_odds is not None or o.under_odds is not None):
                    all_ou_times.append((t, o.goal_line, o.over_odds, o.under_odds, o.bookmaker))

        if all_ou_times:
            # 3.2 共识盘口：取最新时间点最常见的 goal_line
            latest_t = times[-1]
            latest_gls = [gl for (t, gl, ov, un, bm) in all_ou_times if t == latest_t]
            from collections import Counter
            gl_counter = Counter(latest_gls)
            best_gl = gl_counter.most_common(1)[0][0] if gl_counter else None

            if best_gl:
                feats["goal_line_market"] = float(best_gl)

                # 当前水位：分别从有 over/under 数据的记录中取均值
                cur_overs = [ov for (t, gl, ov, un, bm) in all_ou_times
                             if t == latest_t and gl == best_gl and ov is not None]
                cur_unders = [un for (t, gl, ov, un, bm) in all_ou_times
                              if t == latest_t and gl == best_gl and un is not None]
                feats["over_odds_current"] = self._safe_mean(cur_overs)
                feats["under_odds_current"] = self._safe_mean(cur_unders)

            # 3.3 盘口极值与波动
            all_gls = [gl for (t, gl, ov, un, bm) in all_ou_times]
            feats["goal_line_max"] = max(all_gls) if all_gls else 0.0
            feats["goal_line_min"] = min(all_gls) if all_gls else 0.0
            feats["goal_line_volatility"] = self._safe_std(all_gls)

            # 盘口从峰值回落（正向=市场在降盘，倾向小球）
            if best_gl and feats["goal_line_max"] > 0:
                feats["goal_line_drop_from_peak"] = feats["goal_line_max"] - float(best_gl)

            # 3.4 水位时序变化：按博彩公司追踪 over_odds 变化
            over_moves = []
            under_moves = []
            gl_moves = []
            for bm, snaps in by_bm.items():
                ou_snaps = [(s.snapshot_time, s.goal_line, s.over_odds, s.under_odds)
                            for s in snaps if s.goal_line is not None
                            and (s.over_odds is not None or s.under_odds is not None)]
                if len(ou_snaps) >= 2:
                    first = ou_snaps[0]
                    last = ou_snaps[-1]
                    if first[2] is not None and last[2] is not None:
                        over_moves.append(first[2] - last[2])  # 正值=水位下降
                    if first[3] is not None and last[3] is not None:
                        under_moves.append(first[3] - last[3])
                    if first[1] is not None and last[1] is not None:
                        gl_moves.append(last[1] - first[1])  # 正值=盘口上升

            feats["over_odds_movement"] = self._safe_mean(over_moves)
            feats["under_odds_movement"] = self._safe_mean(under_moves)
            feats["goal_line_change"] = self._safe_mean(gl_moves)

            # 3.5 大球水位小时级下降速率（V4.6 新增）
            # 用最近 6 小时内的数据计算 over_odds 每小时的下降速率
            over_time_series = sorted(set(
                (t, ov) for (t, gl, ov, un, bm) in all_ou_times
                if ov is not None and gl == best_gl
            ), key=lambda x: x[0])
            if len(over_time_series) >= 2 and best_gl:
                # 取最近 6 小时窗口
                from datetime import timedelta
                cutoff = over_time_series[-1][0] - timedelta(hours=6)
                recent = [(t, ov) for (t, ov) in over_time_series if t >= cutoff]
                if len(recent) >= 2:
                    first_t, first_ov = recent[0]
                    last_t, last_ov = recent[-1]
                    hours = max((last_t - first_t).total_seconds() / 3600, 0.5)
                    rate = (first_ov - last_ov) / hours  # 正值=水位下降
                    # 归一化：除以初始水位得到相对变化率
                    if first_ov > 0:
                        feats["over_odds_decline_rate"] = rate / first_ov
                    else:
                        feats["over_odds_decline_rate"] = rate

        # ═══════════════════════════════════════════════
        # 维度 4: 共识度特征
        # ═══════════════════════════════════════════════

        # 4.1 1X2 共识方向：各家博彩公司主胜赔率变动方向的一致性
        if movements_h:
            signs = [1 if m > 0.01 else (-1 if m < -0.01 else 0) for m in movements_h]
            if signs:
                feats["odds_consensus_direction"] = self._safe_mean(signs)
                # 范围 [-1, 1]：+1=所有公司一致降主胜赔（看好主队），-1=一致升

        # 4.2 亚盘共识方向
        if hcp_move_h:
            hcp_signs = [1 if m > 0.01 else (-1 if m < -0.01 else 0) for m in hcp_move_h]
            if hcp_signs:
                feats["handicap_consensus_direction"] = self._safe_mean(hcp_signs)

        # 4.3 JC 让球线与市场的偏离（结合外部特征，此处先设 0，由 _compute_deviation 或外部填充）
        # handicap_line_jc_diff 和 goal_line_jc_diff 在 _compute_contextual_deviation 中计算

        return feats

    def _compute_contextual_deviation(self, features: dict) -> dict:
        """V4: 计算 JC 与市场之间的偏离特征（在 _compute_deviation 之后调用）"""
        extra = {
            "handicap_line_jc_diff": 0.0,
            "goal_line_jc_diff": 0.0,
        }

        jc_handicap = features.get("handicap_line", 0.0)
        market_handicap = features.get("handicap_line_market", 0.0)
        if market_handicap != 0.0:
            extra["handicap_line_jc_diff"] = jc_handicap - market_handicap

        # goal_line_jc_diff: JC 没有直接的大小球线，用 expected_goals（来自预测上下文）近似
        # 此值由 PredictionPipeline 在外层填入，此处不处理

        return extra

    @staticmethod
    def _compute_deviation(features: dict) -> dict:
        """L3 偏离度：市场隐含概率 vs 纯数据基线概率（6 维）

        基线：
        - 主胜基线 = home_win_rate（无则 1/3）
        - 平局基线 = 0.25（全球联赛平局率约 25%）
        - 客胜基线 = away_win_rate（无则 1/3）
        """
        market_h = features.get("odds_market_home_prob", 1/3)
        market_d = features.get("odds_market_draw_prob", 1/3)
        market_a = features.get("odds_market_away_prob", 1/3)

        base_h = features.get("home_win_rate", 1/3)
        base_a = features.get("away_win_rate", 1/3)
        base_d = 0.25

        dev_h = market_h - base_h
        dev_d = market_d - base_d
        dev_a = market_a - base_a

        return {
            "deviation_home": round(dev_h, 6),
            "deviation_draw": round(dev_d, 6),
            "deviation_away": round(dev_a, 6),
            "deviation_abs_max": round(max(abs(dev_h), abs(dev_d), abs(dev_a)), 6),
            "deviation_home_sign": 1.0 if dev_h > 0 else (-1.0 if dev_h < 0 else 0.0),
            "deviation_direction": 1.0 if abs(dev_h) >= abs(dev_a) else -1.0,
        }

    @staticmethod
    def _extract_rank_features(home_stats, away_stats, league_baseline: dict, features: dict = None) -> dict:
        """V4: 联赛排名特征（跨联赛可比：用排名分位而非绝对排名）
        
        V4.1: league_position 缺失时，从 points_per_game 推算排名分位
        """
        if features is None:
            features = {}
        feats = {
            "home_league_position": 0,
            "away_league_position": 0,
            "home_rank_percentile": 0.5,
            "away_rank_percentile": 0.5,
            "rank_percentile_diff": 0.0,
        }

        # 联赛球队总数估算
        total_teams = league_baseline.get("league_count", 200) / 19 if league_baseline else 20
        total_teams = max(total_teams, 8)

        # 联赛平均 PPG（用于推算排名）
        lhw = league_baseline.get("league_home_win_rate", 0.45) if league_baseline else 0.45
        ld = league_baseline.get("league_draw_rate", 0.25) if league_baseline else 0.25
        league_avg_ppg = lhw * 3 + ld  # 约 1.6

        def _estimate_percentile(ppg, avg_ppg, total):
            """从 PPG 推算排名分位：PPG/avg 比值映射到 0.05~0.95"""
            if ppg <= 0 or avg_ppg <= 0:
                return 0.5
            ratio = ppg / avg_ppg
            # sigmoid 映射：ratio=1.0 → 0.5, ratio=2.0 → ~0.88, ratio=0.5 → ~0.12
            import math
            percentile = 1.0 / (1.0 + math.exp(-3.0 * (ratio - 1.0)))
            return max(0.05, min(0.95, percentile))

        # 主队
        if home_stats and home_stats.league_position:
            pos = home_stats.league_position
            feats["home_league_position"] = float(pos)
            feats["home_rank_percentile"] = 1.0 - (pos - 1) / total_teams
        else:
            home_ppg = features.get("home_points_per_game", league_avg_ppg)
            feats["home_rank_percentile"] = _estimate_percentile(home_ppg, league_avg_ppg, total_teams)

        # 客队
        if away_stats and away_stats.league_position:
            pos = away_stats.league_position
            feats["away_league_position"] = float(pos)
            feats["away_rank_percentile"] = 1.0 - (pos - 1) / total_teams
        else:
            away_ppg = features.get("away_points_per_game", league_avg_ppg)
            feats["away_rank_percentile"] = _estimate_percentile(away_ppg, league_avg_ppg, total_teams)

        feats["rank_percentile_diff"] = feats["home_rank_percentile"] - feats["away_rank_percentile"]

        return feats

    @staticmethod
    def _extract_sm_predictions(sm_prediction: dict | list | None, features: dict = None) -> dict:
        """V4: SportMonks 官方预测特征（第三方 ensemble 信号）

        SM API 返回 list，每条含 type_id:
          type_id=237: WDL (home/draw/away)
          type_id=234: Over 2.5 (yes/no)
          type_id=231: BTTS (yes/no)
        
        V4.1: SM 预测不可用时，使用市场赔率隐含概率作为 fallback
        """
        # 默认值优先用市场赔率，否则用均匀分布
        use_market = features and features.get("odds_market_home_prob", 0) > 0.01
        feats = {
            "sm_home_prob": features.get("odds_market_home_prob", 1/3) if use_market else 1/3,
            "sm_draw_prob": features.get("odds_market_draw_prob", 1/3) if use_market else 1/3,
            "sm_away_prob": features.get("odds_market_away_prob", 1/3) if use_market else 1/3,
            "sm_over_2_5_prob": 0.5,
            "sm_btts_prob": 0.5,
        }

        if not sm_prediction:
            return feats

        # 处理 list 格式
        items = sm_prediction
        if isinstance(sm_prediction, dict):
            data = sm_prediction.get("data", sm_prediction)
            items = data if isinstance(data, list) else [data]

        if not isinstance(items, list):
            return feats

        for item in items:
            tid = item.get("type_id")
            preds = item.get("predictions", {})

            if tid == 237:  # WDL
                feats["sm_home_prob"] = float(preds.get("home", 1/3)) / 100
                feats["sm_draw_prob"] = float(preds.get("draw", 1/3)) / 100
                feats["sm_away_prob"] = float(preds.get("away", 1/3)) / 100
            elif tid == 234:  # Over 2.5
                feats["sm_over_2_5_prob"] = float(preds.get("yes", 0.5)) / 100
            elif tid == 231:  # BTTS
                feats["sm_btts_prob"] = float(preds.get("yes", 0.5)) / 100

        return feats

    async def _get_uefa_experience(self, team_id: int) -> dict:
        """V4: 查询球队欧战历史经验（从已完成比赛中筛选 UEFA 赛事）"""
        uefa_keywords = ["UEFA", "Champions", "Europa", "Conference", "欧冠", "欧罗巴", "欧协联"]
        result = await self.db.execute(
            select(
                func.count(Match.id),
                func.sum(case((Match.home_score > Match.away_score, 1), else_=0)),
                func.sum(case((Match.home_score == Match.away_score, 1), else_=0)),
                func.sum(Match.home_score),
                func.sum(Match.away_score),
            ).where(
                or_(
                    Match.home_team_id == team_id,
                    Match.away_team_id == team_id,
                ),
                Match.status == "finished",
                Match.home_score.isnot(None),
                or_(*[Match.venue.ilike(f"%{kw}%") for kw in uefa_keywords]),
            )
        )
        row = result.one()
        count = row[0] or 0
        return {
            "uefa_matches": count,
            "uefa_wins": int(row[1] or 0),
            "uefa_draws": int(row[2] or 0),
            "uefa_goals_for": int(row[3] or 0),
            "uefa_goals_against": int(row[4] or 0),
        }

    @staticmethod
    def _extract_uefa_features(home_uefa: dict, away_uefa: dict) -> dict:
        """V4: 欧战经验特征"""
        feats = {
            "home_uefa_matches": 0,
            "home_uefa_win_rate": 0.0,
            "home_uefa_goal_diff_per_game": 0.0,
            "away_uefa_matches": 0,
            "away_uefa_win_rate": 0.0,
            "away_uefa_goal_diff_per_game": 0.0,
            "uefa_experience_gap": 0.0,
        }

        for side, data, prefix in [("home", home_uefa, "home"), ("away", away_uefa, "away")]:
            if not data:
                continue
            n = data.get("uefa_matches", 0)
            if n > 0:
                feats[f"{prefix}_uefa_matches"] = n
                feats[f"{prefix}_uefa_win_rate"] = data.get("uefa_wins", 0) / n
                gf = data.get("uefa_goals_for", 0)
                ga = data.get("uefa_goals_against", 0)
                feats[f"{prefix}_uefa_goal_diff_per_game"] = (gf - ga) / n

        # 经验差距：主场欧战场次 - 客场欧战场次（正=主队更有经验）
        feats["uefa_experience_gap"] = feats["home_uefa_matches"] - feats["away_uefa_matches"]

        return feats
