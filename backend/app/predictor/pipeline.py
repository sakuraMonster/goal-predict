"""预测 Pipeline：整合模型A+B + 比分推导 + 报告生成"""
import json
import numpy as np
import pandas as pd
from scipy.stats import poisson
from sqlalchemy.ext.asyncio import AsyncSession
from app.predictor.features_a import FeatureEngineerA
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB
from app.predictor.models.model_c import ModelC
from app.predictor.models.model_d import ModelD
from app.predictor.snap import snap_top2
from app.collector.sportmonks.client import SportMonksClient
from app import ou_flags

# ── 特征名 → 中文名映射 ──
FEATURE_NAME_CN: dict[str, str] = {
    "home_win_rate": "主队赛季胜率", "home_draw_rate": "主队平局率", "home_goals_avg": "主队场均进球",
    "home_goals_against_avg": "主队场均失球", "home_home_win_rate": "主队主场胜率",
    "home_clean_sheet_rate": "主队零封率", "home_xG": "主队预期进球xG", "home_xGA": "主队预期失球xGA",
    "home_points_per_game": "主队场均积分",
    "away_win_rate": "客队赛季胜率", "away_goals_avg": "客队场均进球",
    "away_goals_against_avg": "客队场均失球", "away_away_win_rate": "客队客场胜率",
    "away_xG": "客队预期进球xG", "away_xGA": "客队预期失球xGA",
    "away_points_per_game": "客队场均积分",
    "win_rate_diff": "胜率差(主-客)", "goals_avg_diff": "进球差(主-客)",
    "xG_diff": "xG差值(主-客)", "points_per_game_diff": "场均积分差(主-客)",
    "league_id": "联赛ID", "season_stage": "赛季阶段", "is_weekend": "是否周末",
    "home_games_played": "主队已赛场次", "away_games_played": "客队已赛场次",
    "points_diff": "积分差距", "handicap_line": "让球线",
    "home_motivation": "主队战意", "away_motivation": "客队战意", "motivation_diff": "战意差",
    "h2h_match_count": "交锋场次",
    "has_h2h": "有交锋记录",
    "h2h_avg_home_xg": "交锋主队xG", "h2h_avg_away_xg": "交锋客队xG",
    "h2h_avg_xg_diff": "交锋xG差", "h2h_avg_home_shots": "交锋主队射门",
    "h2h_avg_away_shots": "交锋客队射门", "h2h_avg_shots_ratio": "交锋射门比",
    "h2h_avg_home_possession": "交锋主队控球", "h2h_avg_home_dangerous": "交锋主队威胁",
    "h2h_avg_away_dangerous": "交锋客队威胁",
    "odds_home_initial": "初盘主胜赔率", "odds_draw_initial": "初盘平局赔率", "odds_away_initial": "初盘客胜赔率",
    "odds_home_current": "即时主胜赔率", "odds_draw_current": "即时平局赔率", "odds_away_current": "即时客胜赔率",
    "odds_movement_home": "赔率变动-主胜", "odds_movement_draw": "赔率变动-平局", "odds_movement_away": "赔率变动-客胜",
    "odds_implied_home_change": "隐含概率变化-主胜", "odds_std_home": "赔率离散度",
    "draw_odds_current": "即时平赔绝对值", "odds_dispersity": "赔率分歧度",
    "odds_market_home_prob": "市场隐含主胜概率", "odds_market_draw_prob": "市场隐含平局概率",
    "odds_market_away_prob": "市场隐含客胜概率",
    "home_injuries": "主队伤停人数", "away_injuries": "客队伤停人数",
    "home_rest_days": "主队休息天数", "away_rest_days": "客队休息天数", "rest_days_diff": "休息天数差",
    "home_form_pts_6": "主队近6场场均积分", "home_form_pts_10": "主队近10场场均积分",
    "home_gf_avg_6": "主队近6场场均进球", "home_ga_avg_6": "主队近6场场均失球",
    "home_form_trend": "主队状态趋势", "home_home_form_pts": "主队近6场主场场均积分",
    "home_away_form_pts": "主队近6场客场场均积分",
    "away_form_pts_6": "客队近6场场均积分", "away_form_pts_10": "客队近10场场均积分",
    "away_gf_avg_6": "客队近6场场均进球", "away_ga_avg_6": "客队近6场场均失球",
    "away_form_trend": "客队状态趋势", "away_home_form_pts": "客队近6场主场场均积分",
    "away_away_form_pts": "客队近6场客场场均积分",
    "deviation_home": "市场偏离度-主胜", "deviation_draw": "市场偏离度-平局",
    "deviation_away": "市场偏离度-客胜", "deviation_abs_max": "最大偏离度",
    "deviation_home_sign": "偏离方向-主胜", "deviation_direction": "偏离主导方向",
}

# ── 特征方向判定：哪些特征高值利好主胜/客胜 ──
FEATURE_DIRECTION: dict[str, str] = {
    "home_win_rate": "home", "home_goals_avg": "home", "home_home_win_rate": "home",
    "home_clean_sheet_rate": "home", "home_xG": "home", "home_points_per_game": "home",
    "home_motivation": "home", "home_form_pts_6": "home", "home_form_pts_10": "home",
    "home_gf_avg_6": "home", "home_form_trend": "home",
    "home_home_form_pts": "home", "home_away_form_pts": "home",
    "away_goals_against_avg": "home",
    "away_win_rate": "away", "away_goals_avg": "away", "away_away_win_rate": "away",
    "away_xG": "away", "away_points_per_game": "away",
    "away_motivation": "away", "away_form_pts_6": "away", "away_form_pts_10": "away",
    "away_gf_avg_6": "away", "away_form_trend": "away",
    "away_home_form_pts": "away", "away_away_form_pts": "away",
    "home_goals_against_avg": "away",
    "win_rate_diff": "home", "points_diff": "home", "points_per_game_diff": "home",
    "handicap_line": "home", "motivation_diff": "home",
    "h2h_home_win_rate": "home", "h2h_home_wins": "home",
    "odds_movement_home": "home", "odds_movement_away": "away",
    "odds_implied_home_change": "home",
    "odds_std_home": "cold",
    "home_injuries": "away", "away_injuries": "home",
    "deviation_direction": "home",
}

# 对进球数方向特别重要的特征
GOALS_RELEVANT_FEATURES = [
    "home_goals_avg", "away_goals_avg", "home_xG", "away_xGA",
    "home_gf_avg_6", "away_ga_avg_6",
    "odds_market_home_prob", "odds_market_away_prob",
    "home_form_trend", "away_form_trend",
    "h2h_avg_home_xg", "h2h_avg_away_xg",
]


class PredictionPipeline:
    # ── V4.9 赔率变动幅度分档阈值（可调参数）──
    # market_home_score = odds_mv_home - odds_mv_away + impl_change * 3
    MARKET_SIGNAL_NEUTRAL = 0.015      # 低于此值视为方向不明
    MARKET_SIGNAL_SMALL  = 0.05        # 低于此值视为"小幅"
    MARKET_SIGNAL_LARGE  = 0.15        # 高于此值视为"大幅"，中间为"中幅"
    MARKET_SIGNAL_AMPLIFY_MAX = 0.20   # amp_factor 归一化分母：signal/this → 0~1
    
    # 分歧增强权重系数
    DIV_COUNTER_WEIGHT_BASE = 0.4      # 模型-市场矛盾时的基础权重
    DIV_COUNTER_WEIGHT_AMP  = 0.5      # 矛盾时幅度放大系数（上限 = base + amp）
    DIV_AGREE_WEIGHT_BASE   = 0.2      # 方向一致时的基础权重
    DIV_AGREE_WEIGHT_AMP    = 0.2      # 一致时幅度放大系数
    DIV_NEUTRAL_WEIGHT      = 0.4      # 方向不明时压平局的权重
    
    # 综合信号权重
    IMPL_CHANGE_WEIGHT = 3.0           # impl_change 在 market_home_score 中的权重

    # V4.11b: 联赛后验校准系数 —— 基于回测 optimal ratio + 0.08 margin
    LEAGUE_LAMBDA_CALIBRATION = {
        "美职联": 0.65,    # V4.12: λ均值4.16→实际大球率43%, ratio≈0.55 + margin
        "芬超":   0.86,    # ratio 0.71 + margin
        "瑞典超": 0.96,    # ratio 0.88 + margin
        "挪超":   1.10,    # 高比分联赛，ratio 1.26, 保守取1.10
        "巴甲":   0.85,    # V4.12: raw_λ 均值4.99但极差大，保守校准，避免压制正确低λ预测
        "欧冠":   1.00,    # V4.12: 欧战数据充分，无需校准
        "韩K":    1.00,    # V4.12: raw_λ 均值1.81，无需校准（×0.90曾制造校准诱发失误）
        "葡超":   0.92,    # 2026-08: 新赛季市场盘口系统性高估(λ/实=1.18, 8场样本)，SNAP top2 无法覆盖1球，温和后验
    }
    # V4.13: Model C 专属后验校准 —— 基于 Model C 自身 λ/实 偏差回测
    # 葡超: λ/实=1.18 (8场样本, 修复后), SNAP top2 无法覆盖1球 → 温和后验 0.92 (38%→50%)
    # 独立于 LEAGUE_LAMBDA_CALIBRATION (Model B)，避免双重计数；仅覆盖有充分证据的联赛
    MODELC_LAMBDA_CALIBRATION = {
        "葡超": 0.92,
    }
    # V4.12: 联赛级市场衰减参数 (max_attenuation, min_attenuation)
    # 默认: (0.08, 0.03) — 均匀时衰减8%，极端时衰减3%
    LEAGUE_MARKET_ATTENUATION = {
        "韩K": (0, 0),  # V4.12: 韩K不做市场衰减，反向增强中已有足够信号调整
    }
    # V4.12: 联赛级盘口调整权重（-1=反向增强，0=跳过，1=正常跟随）
    # 默认 1.0，韩K 设为 -1：市场诱导小球 → 反向推高λ看大球，反之同理
    LEAGUE_MARKET_ADJUSTMENT_WEIGHT = {
        "韩K": -1.0,
    }
    # V4.12: 杯赛分包校准 —— 按实际杯赛类型分别控制
    CUP_CALIB_BRAZIL  = 0.75   # 巴西杯赛：与巴甲接近
    CUP_CALIB_EUROPE  = 0.65   # 欧战杯赛：raw_λ 严重虚高（均值3.82），需强力压制

    _BR_TEAMS = {"桑托斯", "米拉索尔", "格雷米奥", "巴西国际", "科林蒂安",
                 "巴拉纳", "维多利亚", "弗拉门戈", "帕尔梅拉斯", "圣保罗",
                 "弗鲁米嫩", "巴伊亚", "里莫", "克鲁塞罗", "博塔弗戈", "戈亚斯"}

    def __init__(self, db: AsyncSession):
        self.db = db
        self.feat_a = FeatureEngineerA(db)
        self.feat_b = FeatureEngineerB(db)
        self.model_a = ModelA()
        self.model_b = ModelB()
        self.model_c = ModelC()
        self.model_d = ModelD()

    def _detect_cup_calibration(self, match) -> tuple[str, float]:
        """检测未知联赛的实际杯赛类型，返回 (标签, 校准系数)
        注意：韩K联已提升为标准联赛，不在此处理"""
        # 收集所有可用队名
        home_names = {
            match.home_team_name or "",
            match.home_team.name_zh if match.home_team else "",
        }
        away_names = {
            match.away_team_name or "",
            match.away_team.name_zh if match.away_team else "",
        }

        # 巴西杯赛检测：双方均为巴西球队
        is_br_home = any(t in n for n in home_names for t in self._BR_TEAMS)
        is_br_away = any(t in n for n in away_names for t in self._BR_TEAMS)
        if is_br_home and is_br_away:
            return ("巴西杯赛", self.CUP_CALIB_BRAZIL)

        # 默认：欧战杯赛
        return ("欧战杯赛", self.CUP_CALIB_EUROPE)

    async def predict(self, match_id: int) -> dict:
        # 获取 match 的 sportmonks_fixture_id 和 league_id（用于联赛校准）
        from sqlalchemy import select as sa_select
        from app.db.models import Match as MatchModel, League
        from sqlalchemy.orm import joinedload
        match_result = await self.db.execute(
            sa_select(MatchModel).options(
                joinedload(MatchModel.league)
            ).where(MatchModel.id == match_id)
        )
        match = match_result.unique().scalar_one_or_none()
        sm_fixture_id = match.sportmonks_fixture_id if match else None
        league_name = match.league.name_zh if match and match.league else None

        # 拉取 SM 官方预测（作为 ensemble 特征）
        sm_prediction = None
        if sm_fixture_id:
            sm_client = None
            try:
                sm_client = SportMonksClient()
                sm_prediction = await sm_client.get_predictions_by_fixture(sm_fixture_id)
                if not sm_prediction or (isinstance(sm_prediction, dict) and not sm_prediction.get("data")):
                    print(f"[SM_PREDICT] fixture={sm_fixture_id}: API returned empty data", flush=True)
                    sm_prediction = None
            except Exception as e:
                print(f"[SM_PREDICT] fixture={sm_fixture_id} FAILED: {type(e).__name__}: {e}", flush=True)
                sm_prediction = None
            finally:
                if sm_client:
                    try:
                        await sm_client.close()
                    except Exception:
                        pass

        features_a_df = await self.feat_a.extract_features(match_id, sm_prediction)
        features_b_df = await self.feat_b.extract_features(match_id, sm_prediction)
        if features_a_df.empty or features_b_df.empty:
            return self._empty_result()

        result_a = self.model_a.predict(features_a_df)
        result_b = self.model_b.predict(features_b_df)

        features_a = features_a_df.iloc[0].to_dict()
        features_b = features_b_df.iloc[0].to_dict()

        # Model C & D: 新模型并行预测（与 B 共享 features_b，不参与 B 的调整链）
        result_c = self.model_c.predict(features_b, league_name)
        result_d = self.model_d.predict(features_b, league_name)

        # V4.13: Model C 专属后验校准（仅葡超，证据充分；不随 Model B 的 calib map，避免双重计数）
        if league_name and league_name in self.MODELC_LAMBDA_CALIBRATION:
            mc_calib = self.MODELC_LAMBDA_CALIBRATION[league_name]
            old_lambda_c = result_c["expected_goals"]
            result_c = self._apply_lambda_calibration(result_c, mc_calib)
            print(f"[CALIB_C] {league_name}: λ_c {old_lambda_c:.2f} → {result_c['expected_goals']:.2f} (×{mc_calib})", flush=True)

        is_cold = self._is_cold_match(result_a, features_a)
        if is_cold:
            result_a = self._apply_cold_correction(result_a, features_a)

        # V4.7: 大小球盘口趋势融合——用市场盘口信号调整预期进球
        result_b = self._apply_goal_market_adjustment(result_b, features_b, league_name)

        # V4.11: 推理时市场信号衰减 —— 当市场概率接近均匀分布时压缩 λ
        result_b = self._apply_market_attenuation(result_b, features_b, league_name)

        # V4.11: 联赛后验校准 —— 按联赛历史偏差缩放 λ
        calib = None
        calib_label = None
        if league_name and league_name in self.LEAGUE_LAMBDA_CALIBRATION:
            calib = self.LEAGUE_LAMBDA_CALIBRATION[league_name]
            calib_label = league_name
        else:
            # V4.12: 杯赛分包校准（league_name 为空或不在标准联赛中）
            calib_label, calib = self._detect_cup_calibration(match)

        if calib is not None:
            old_lambda = result_b["expected_goals"]
            result_b = self._apply_lambda_calibration(result_b, calib)
            print(f"[CALIB] {calib_label}: λ {old_lambda:.2f} → {result_b['expected_goals']:.2f} (×{calib})", flush=True)

        # 让球概率：完全由 Model A 独立分类器预测，不与 Model B 混合
        # 目的：Model A 和 Model B 调参互不影响

        score_top5 = self._derive_scores(
            result_a["home_prob"], result_a["draw_prob"], result_a["away_prob"],
            result_b["expected_goals"]
        )

        confidence = self._calc_confidence(result_a, is_cold)
        # L3: 早季期置信度降级 —— 任一队已赛场次<5时基本面样本不足，强制降为 low
        if confidence != "low":
            home_played = int(features_a.get("home_games_played", 0) or 0)
            away_played = int(features_a.get("away_games_played", 0) or 0)
            if home_played < 5 or away_played < 5:
                confidence = "low"
        data_quality = self._detect_data_quality(features_a, features_b, match)

        return {
            **result_a, **result_b,
            "score_top5_json": score_top5,
            "snap_top2": snap_top2(result_b["expected_goals"]),
            "is_cold_match": is_cold,
            "confidence_level": confidence,
            "data_quality": data_quality,
            "summary_text": self._generate_summary(result_a, result_b, is_cold),
            "key_factors": json.dumps(self._generate_key_factors(features_a, features_b, result_a, result_b, is_cold), ensure_ascii=False),
            # Model C & D 并行结果
            "expected_goals_c": result_c["expected_goals"],
            "expected_goals_d": result_d["expected_goals"],
            "snap_top2_c": snap_top2(result_c["expected_goals"]),
            "snap_top2_d": snap_top2(result_d["expected_goals"]),
        }

    def _apply_goal_market_adjustment(self, result_b: dict, features: dict, league_name: str = None) -> dict:
        """V4.11b: 大小球盘口趋势融合——双向修正
        
        向下修正（原V4.7）：盘口回落/水位下跌 → 拉低 λ
        向上修正（V4.11b新增）：盘口明确看大球且模型偏低 → 拉高 λ
        
        V4.12: 联赛级反向增强 —— 韩K 权重=-1，市场方向反转：
               市场诱导小球 → 反向推高λ看大球；市场诱大球 → 反向压低λ看小球
        """
        adj_weight = self.LEAGUE_MARKET_ADJUSTMENT_WEIGHT.get(league_name, 1.0)
        # weight=0: 完全跳过
        if adj_weight == 0:
            return dict(result_b)
        
        result = dict(result_b)
        lambda_val = result.get("expected_goals", 2.5)
        
        goal_line = features.get("goal_line_market", 0) or 0
        goal_drop = features.get("goal_line_drop_from_peak", 0) or 0
        goal_max = features.get("goal_line_max", 0) or 0
        over_move = features.get("over_odds_movement", 0) or 0
        over_decline = features.get("over_odds_decline_rate", 0) or 0
        goal_vol = features.get("goal_line_volatility", 0) or 0

        # ── V5 新回落信号（OU_NEW_MODEL_THRESHOLDS 开启时使用） ──
        odds_drift = features.get("odds_drift_over_mean", 0) or 0
        drift_consensus = features.get("odds_drift_consensus", 0) or 0
        gl_shift = features.get("goal_line_shift", 0) or 0
        if ou_flags.OU_NEW_MODEL_THRESHOLDS:
            # 新量纲：同线水位漂移 + 整线位移
            # 韩K反向逻辑基于新信号：水位大幅下跌 + 高度一致 → 市场诱小球
            goal_drop = gl_shift          # 整线位移替代旧的跨线差值
            goal_max = max(goal_line, goal_line + gl_shift)  # 峰值 = 当前线 + 位移
            over_move = odds_drift * 10   # 水位漂移映射到旧 over_move 量纲
            over_decline = over_decline   # 衰减速率沿用
        
        # 无有效盘口数据 → 不调整
        if goal_line < 0.5 or goal_max < 0.5:
            return result
        
        market_lambda = goal_line
        
        # V4.12: 韩K盘口回落分档（仅韩K，其他联赛正常跟随）
        # goal_drop < 1.0: 无明确信号，跳过
        # 1.0 ~ 1.25: 小幅回落 → 诱导，反向增强
        # >= 1.25: 大幅回落 → 真实市场方向，正常跟随
        invert = False
        if adj_weight < 0:  # 韩K
            if ou_flags.OU_NEW_MODEL_THRESHOLDS:
                # 新量纲：gl_shift≈0~1.5, odds_drift≈0~0.3
                # 无明确信号：整线位移小 且 水位漂移弱
                if gl_shift < 0.5 and odds_drift < 0.03:
                    return result
                # 小幅回落 → 诱导，反向增强
                elif gl_shift < 1.0 or (0.03 <= odds_drift < 0.08):
                    invert = True
                # 大幅回落 → 真实市场方向，正常跟随
            else:
                if goal_drop < 1.0:
                    return result
                elif goal_drop < 1.25:
                    invert = True
            # goal_drop >= 1.25: invert=False, 走正常跟随市场逻辑
        
        # ── V4.11b: 向上修正 —— 盘口明确看大球 + 模型明显偏低 ──
        # 不依赖 drop_strength，独立触发
        if market_lambda >= 2.5 and lambda_val < market_lambda * 0.7:
            # 模型λ严重低于市场盘口 → 向上拉近
            gap_ratio = (market_lambda - lambda_val) / max(market_lambda, 1.0)
            market_weight = min(0.15 + 0.25 * gap_ratio, 0.40)  # 15%~40% 市场
            model_weight = 1.0 - market_weight
            adjusted_lambda = model_weight * lambda_val + market_weight * market_lambda
            
            if invert:
                # 韩K反向：市场诱大球 → 压低λ看小球
                adjusted_lambda = lambda_val - market_weight * (market_lambda - lambda_val)
                result["expected_goals"] = max(adjusted_lambda, lambda_val * 0.80)
            else:
                result["expected_goals"] = min(adjusted_lambda, market_lambda * 1.2)
            
            from scipy.stats import poisson
            new_lambda = result["expected_goals"]
            goal_probs = [float(poisson.pmf(k, new_lambda)) for k in range(5)]
            goal_probs[4] = float(1.0 - poisson.cdf(3, new_lambda))
            total = sum(goal_probs)
            result["goal_distribution"] = [p / total for p in goal_probs]
            result["over_2_5_prob"] = float(1.0 - poisson.cdf(2, new_lambda))
            
            return result  # 向上修正后直接返回，不再走下面的向下逻辑
        
        # ── 向下修正：盘口趋势信号 ──
        # goal_drop > 0 表示盘口从峰值回落
        # over_move > 0 表示大球水位下降
        drop_strength = 0.0
        if goal_drop > 0.5:
            # 盘口回落幅度越大，信号越强（归一化：drop / max）
            drop_strength = min(goal_drop / max(goal_max, 1.0), 1.0)
        if over_move > 0.3:
            # 水位下跌也加分
            drop_strength = max(drop_strength, min(over_move / 2.0, 0.8))
        if over_decline > 0.002:
            # 水位小时级下降速率
            drop_strength = max(drop_strength, min(over_decline * 50, 0.6))

        # V5: 新信号一致性加成（水位漂移方向一致时加强）
        if ou_flags.OU_NEW_MODEL_THRESHOLDS and drift_consensus > 0.7 and odds_drift > 0.03:
            drop_strength = min(drop_strength * 1.3, 0.9)
        
        # 盘口波动大 → 降低调整力度（市场自己也不确定）
        if goal_vol > 1.5:
            drop_strength *= 0.5
        
        # ── 融合调整 ──
        if drop_strength > 0.05 and lambda_val > market_lambda:
            # 盘口信号看小球 + 模型预测过高 → 向下拉动
            # 权重：模型 70% + 市场 30%*drop_strength，最大不超过 50% 市场
            market_weight = min(0.30 * drop_strength, 0.50)
            model_weight = 1.0 - market_weight
            adjusted_lambda = model_weight * lambda_val + market_weight * market_lambda
            
            if invert:
                # 韩K反向：市场诱导小球 → goal_drop驱动推高λ看大球
                market_weight_rev = min(0.60 * drop_strength, 0.65)
                adjusted_lambda = lambda_val + market_weight_rev * goal_drop
                result["expected_goals"] = min(adjusted_lambda, lambda_val * 1.30)
            else:
                result["expected_goals"] = max(adjusted_lambda, market_lambda * 0.8)
            
            # 重新计算进球分布
            from scipy.stats import poisson
            new_lambda = result["expected_goals"]
            goal_probs = [float(poisson.pmf(k, new_lambda)) for k in range(5)]
            goal_probs[4] = float(1.0 - poisson.cdf(3, new_lambda))
            total = sum(goal_probs)
            result["goal_distribution"] = [p / total for p in goal_probs]
            result["over_2_5_prob"] = float(1.0 - poisson.cdf(2, new_lambda))
        
        elif drop_strength > 0.05 and lambda_val < market_lambda:
            # 盘口信号看大球 + 模型预测过低 → 向上拉动
            market_weight = min(0.20 * drop_strength, 0.35)
            model_weight = 1.0 - market_weight
            adjusted_lambda = model_weight * lambda_val + market_weight * market_lambda
            
            if invert:
                # 韩K反向：市场诱大球 → 压低λ看小球
                adjusted_lambda = lambda_val - market_weight * (market_lambda - lambda_val)
                result["expected_goals"] = max(adjusted_lambda, lambda_val * 0.80)
            else:
                result["expected_goals"] = min(adjusted_lambda, market_lambda * 1.2)
            
            from scipy.stats import poisson
            new_lambda = result["expected_goals"]
            goal_probs = [float(poisson.pmf(k, new_lambda)) for k in range(5)]
            goal_probs[4] = float(1.0 - poisson.cdf(3, new_lambda))
            total = sum(goal_probs)
            result["goal_distribution"] = [p / total for p in goal_probs]
            result["over_2_5_prob"] = float(1.0 - poisson.cdf(2, new_lambda))
        
        return result

    @staticmethod
    def _zip_distribution(lam: float, p_zero: float) -> list:
        """V4.11: Zero-Inflated Poisson 分布计算"""
        from scipy.stats import poisson
        base = [float(poisson.pmf(k, lam)) for k in range(5)]
        base[4] = float(1.0 - poisson.cdf(3, lam))
        mix = [(1.0 - p_zero) * p for p in base]
        mix[0] += p_zero
        total = sum(mix)
        return [p / total for p in mix]

    def _apply_market_attenuation(self, result_b: dict, features: dict, league_name: str = None) -> dict:
        """V4.11: 推理时市场信号衰减 —— 压缩市场概率对 λ 的过度影响
        
        当市场概率接近 0.33（均匀分布，无信息）时，市场信号衰减力度最大。
        当市场概率极端（>0.6 或 <0.2）时，衰减力度减小，保留市场方向信号。
        
        衰减公式: λ_adj = λ * (1 - attenuation_factor)
        attenuation_factor = max_attenuation * (1 - |market_prob_diff|)
        """
        from scipy.stats import poisson
        
        market_h = features.get("odds_market_home_prob", 0.33)
        market_a = features.get("odds_market_away_prob", 0.33)
        
        # 市场概率偏离均匀分布的程度
        prob_diff = abs(market_h - 1.0/3.0) + abs(market_a - 1.0/3.0)
        # max_diff 理论最大 = 2 * (1 - 1/3) ≈ 1.33
        max_diff = 2.0 * (1.0 - 1.0/3.0)
        # 归一化：0 = 完全均匀（衰减最大），1 = 极端偏离（衰减最小）
        norm_diff = min(prob_diff / max_diff, 1.0)
        
        # 衰减强度：均匀时衰减8%，极端时衰减3%（V4.11b: 降低避免过度压制）
        # V4.12: 联赛级覆盖
        max_attenuation, min_attenuation = self.LEAGUE_MARKET_ATTENUATION.get(
            league_name, (0.08, 0.03))
        attenuation = max_attenuation - (max_attenuation - min_attenuation) * norm_diff
        
        result = dict(result_b)
        old_lambda = result["expected_goals"]
        new_lambda = old_lambda * (1.0 - attenuation)
        new_lambda = max(new_lambda, 0.1)
        result["expected_goals"] = new_lambda
        
        # V4.11: 保留 ZIP 零膨胀概率
        p_zero = result_b.get("zero_inflation_prob", 0.0)
        if p_zero > 0:
            goal_probs = self._zip_distribution(new_lambda, p_zero)
        else:
            goal_probs = [float(poisson.pmf(k, new_lambda)) for k in range(5)]
            goal_probs[4] = float(1.0 - poisson.cdf(3, new_lambda))
        total = sum(goal_probs)
        result["goal_distribution"] = [p / total for p in goal_probs]
        result["over_2_5_prob"] = float(1.0 - sum(result["goal_distribution"][:3]))
        return result

    @staticmethod
    def _apply_lambda_calibration(result_b: dict, calib: float) -> dict:
        """V4.11: 联赛后验校准 —— 按联赛历史偏差缩放 λ 并重算分布"""
        from scipy.stats import poisson
        result = dict(result_b)
        new_lambda = result["expected_goals"] * calib
        new_lambda = max(new_lambda, 0.1)
        result["expected_goals"] = new_lambda

        # V4.11: 保留 ZIP 零膨胀概率
        p_zero = result_b.get("zero_inflation_prob", 0.0)
        if p_zero > 0:
            goal_probs = self._zip_distribution(new_lambda, p_zero)
        else:
            goal_probs = [float(poisson.pmf(k, new_lambda)) for k in range(5)]
            goal_probs[4] = float(1.0 - poisson.cdf(3, new_lambda))
        total = sum(goal_probs)
        result["goal_distribution"] = [p / total for p in goal_probs]
        result["over_2_5_prob"] = float(1.0 - sum(result["goal_distribution"][:3]))
        return result

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

    def _derive_handicap_probs(self, home_p: float, draw_p: float, away_p: float,
                                expected_goals: float, handicap_line: float) -> dict:
        """从 1X2 概率 + 进球分布推导任意让球线的概率（通用公式）

        通用公式（handicap_line=h，h<0表示主队让|h|球）：
        - 让胜 = P(hg + h > ag) = P(hg - ag > -h)
        - 让平 = P(hg + h = ag) = P(hg - ag = -h)
        - 让负 = P(hg + h < ag) = P(hg - ag < -h)

        实现：用独立 Poisson(λ/2) 生成比分概率空间，在 1X2 三组内按
        让球临界线分配概率。
        """
        if handicap_line is None or handicap_line == 0:
            return {"handicap_home_prob": home_p, "handicap_draw_prob": draw_p, "handicap_away_prob": away_p}

        half_lambda = max(expected_goals / 2, 0.3)
        critical_margin = -handicap_line  # hg - ag 的临界值

        max_g = min(int(expected_goals) + 4, 10)
        # 按 主胜/平/客 分组收集比分概率
        scores = {"home": [], "draw": [], "away": []}
        p_home = p_draw = p_away = 0.0
        for hg in range(max_g + 1):
            for ag in range(max_g + 1):
                prob = poisson.pmf(hg, half_lambda) * poisson.pmf(ag, half_lambda)
                if hg > ag:
                    p_home += prob; scores["home"].append((hg - ag, prob))
                elif hg == ag:
                    p_draw += prob; scores["draw"].append((0, prob))
                else:
                    p_away += prob; scores["away"].append((hg - ag, prob))

        hcp_home = hcp_draw = hcp_away = 0.0

        for outcome, weight, margin_list in [
            ("home", home_p, scores["home"]),
            ("draw", draw_p, scores["draw"]),
            ("away", away_p, scores["away"]),
        ]:
            total_p = p_home if outcome == "home" else p_draw if outcome == "draw" else p_away
            if total_p < 1e-10:
                continue
            for margin, prob in margin_list:
                norm_p = prob / total_p  # 组内归一化
                if margin > critical_margin:
                    hcp_home += weight * norm_p
                elif margin == critical_margin:
                    hcp_draw += weight * norm_p
                else:
                    hcp_away += weight * norm_p

        total = hcp_home + hcp_draw + hcp_away
        if total < 1e-10:
            return {"handicap_home_prob": 0.33, "handicap_draw_prob": 0.34, "handicap_away_prob": 0.33}

        return {
            "handicap_home_prob": float(hcp_home / total),
            "handicap_draw_prob": float(hcp_draw / total),
            "handicap_away_prob": float(hcp_away / total),
        }

    def _is_cold_match(self, result_a: dict, features: dict) -> bool:
        """V4.3 冷门检测：市场vs模型分歧 + 赔率分歧度（阶梯阈值 + 杯赛感知）
        
        信号层级：
        1. 市场方向 vs 模型方向分歧
        2. 庄家间赔率分歧度（阶梯触发）
        3. 基本面与赔率背离
        4. 杯赛/非联赛：赛季数据不可靠 → 降低触发阈值
        """
        # 1. 市场隐含概率
        market_home = features.get("odds_market_home_prob", 1/3)
        market_draw = features.get("odds_market_draw_prob", 1/3)
        market_away = features.get("odds_market_away_prob", 1/3)

        # 2. 模型预测
        model_home = result_a["home_prob"]
        model_draw = result_a["draw_prob"]
        model_away = result_a["away_prob"]

        # 3. 市场vs模型方向分歧
        market_max_dir = max(
            ("home", market_home), ("draw", market_draw), ("away", market_away),
            key=lambda x: x[1]
        )
        model_in_market_dir = {
            "home": model_home, "draw": model_draw, "away": model_away
        }[market_max_dir[0]]

        market_confident = market_max_dir[1] > 0.45
        model_disagrees = model_in_market_dir < 0.35
        
        # 4. 杯赛/非联赛检测：赛季数据不可靠，降低触发阈值
        home_gp = features.get("home_games_played", 10) or 10
        away_gp = features.get("away_games_played", 10) or 10
        is_non_league = (home_gp < 5 or away_gp < 5)
        
        # 5. 阶梯分歧度阈值
        odds_std = features.get("odds_std_home", 0)
        odds_dispersity = features.get("odds_dispersity", 0)
        
        if is_non_league:
            # 杯赛/国家队：赛季胜率参考价值低，更容易触发冷门
            high_divergence = odds_std > 0.03 or odds_dispersity > 0.07
        else:
            # 联赛：正常阈值 + 阶梯
            high_divergence = odds_std > 0.05 or odds_dispersity > 0.07
        
        # 6. 基本面与赔率背离
        fundamental_divergence = features.get("fundamental_odds_divergence", 0)
        fundamental_signal = fundamental_divergence > 5.0
        
        return (market_confident and model_disagrees) or high_divergence or fundamental_signal

    def _apply_cold_correction(self, result_a: dict, features: dict) -> dict:
        """V4.5 冷门概率修正：方向感知分歧度 + bookmaker_intent + 市场融合 + 杯赛感知
        
        四步修正：
        1. 杯赛感知权重：非同赛事+模型推平局 → 大幅增加市场权重
        2. 模型-市场加权融合
        3. 分歧度方向感知增强
        4. 庄家意图指数直接调整概率方向
        """
        market_home = features.get("odds_market_home_prob", 1/3)
        market_draw = features.get("odds_market_draw_prob", 1/3)
        market_away = features.get("odds_market_away_prob", 1/3)
        
        odds_std = features.get("odds_std_home", 0)
        odds_dispersity = features.get("odds_dispersity", 0)
        bookmaker_intent = features.get("bookmaker_intent", 0)
        same_league = features.get("season_match_same_league", 1.0)

        original_home = result_a["home_prob"]
        original_draw = result_a["draw_prob"]
        original_away = result_a["away_prob"]

        # V4.5: 杯赛/非同赛事场景下，赛季数据不可靠，模型过度推平局
        # 按模型对平局的置信度分级调整市场权重
        cup_override = False
        if same_league < 0.5 and original_draw > 0.40:
            if original_draw > 0.50:
                # 模型极度推平局 → 几乎纯市场
                MODEL_WEIGHT = 0.20
                MARKET_WEIGHT = 0.80
                cup_override = True
            else:
                MODEL_WEIGHT = 0.35
                MARKET_WEIGHT = 0.65
                cup_override = True
        else:
            MODEL_WEIGHT = 0.55
            MARKET_WEIGHT = 0.45

        corrected = dict(result_a)
        corrected["home_prob"] = MODEL_WEIGHT * original_home + MARKET_WEIGHT * market_home
        corrected["draw_prob"] = MODEL_WEIGHT * original_draw + MARKET_WEIGHT * market_draw
        corrected["away_prob"] = MODEL_WEIGHT * original_away + MARKET_WEIGHT * market_away
        
        # ── Step 2: 方向感知分歧度增强 ──
        # 杯赛覆盖时跳过：模型平局已膨胀，分歧增强只会让平局更夸张
        direction_label = None
        divergence_boost = 0.0
        is_extreme_divergence = odds_dispersity > 0.15
        
        if not cup_override:
            if odds_std > 0.05:
                divergence_boost = min(0.20, odds_std * 2)
            if odds_dispersity > 0.1:
                divergence_boost = max(divergence_boost, 0.15)
            if 0.07 < odds_dispersity <= 0.1 and divergence_boost == 0:
                divergence_boost = 0.08
            
            if divergence_boost > 0:
                if is_extreme_divergence:
                    corrected["draw_prob"] += divergence_boost
                    if corrected["home_prob"] + corrected["away_prob"] > 0:
                        ratio_h = corrected["home_prob"] / (corrected["home_prob"] + corrected["away_prob"])
                        corrected["home_prob"] -= divergence_boost * ratio_h
                        corrected["away_prob"] -= divergence_boost * (1 - ratio_h)
                    direction_label = "极度分歧→平局"
                else:
                    probs = [corrected["home_prob"], corrected["draw_prob"], corrected["away_prob"]]
                    max_p = max(probs)
                    min_p = max(min(probs), 0.01)
                    balance_ratio = max_p / min_p
                    
                    if corrected["draw_prob"] > corrected["home_prob"] and corrected["draw_prob"] > corrected["away_prob"]:
                        corrected["draw_prob"] += divergence_boost
                        if corrected["home_prob"] + corrected["away_prob"] > 0:
                            ratio_h = corrected["home_prob"] / (corrected["home_prob"] + corrected["away_prob"])
                            corrected["home_prob"] -= divergence_boost * ratio_h
                            corrected["away_prob"] -= divergence_boost * (1 - ratio_h)
                        direction_label = "平局领先→加平局"
                    elif balance_ratio < 1.5:
                        corrected["draw_prob"] += divergence_boost
                        if corrected["home_prob"] + corrected["away_prob"] > 0:
                            ratio_h = corrected["home_prob"] / (corrected["home_prob"] + corrected["away_prob"])
                            corrected["home_prob"] -= divergence_boost * ratio_h
                            corrected["away_prob"] -= divergence_boost * (1 - ratio_h)
                        direction_label = "三方均衡→平局"
                    else:
                        # V4.9: 分歧增强方向基于市场赔率走势幅度
                        # 获取赔率变动特征
                        odds_mv_home = features.get("odds_movement_home", 0) or 0
                        odds_mv_away = features.get("odds_movement_away", 0) or 0
                        impl_change = features.get("odds_implied_home_change", 0) or 0
                        
                        # 市场方向信号（连续值）：
                        # odds_movement = initial - current
                        # 正值 → 赔率下降 → 市场看好；负值 → 赔率上升 → 市场看空
                        # 综合信号 = 主胜方向得分 - 客胜方向得分
                        #   odds_mv_home: 正值=看好主队
                        #   odds_mv_away: 正值=看好客队（对主队为负贡献）
                        #   impl_change: 正值=隐含概率升=看好主队
                        market_home_score = odds_mv_home - odds_mv_away + impl_change * self.IMPL_CHANGE_WEIGHT
                        market_strength = abs(market_home_score)
                        
                        # 市场方向判定
                        if market_strength < self.MARKET_SIGNAL_NEUTRAL:
                            market_direction = "neutral"
                            market_label = "市场方向不明"
                        elif market_home_score > 0:
                            market_direction = "home"
                            if market_strength < self.MARKET_SIGNAL_SMALL:
                                amp = "小幅"
                            elif market_strength < self.MARKET_SIGNAL_LARGE:
                                amp = "中幅"
                            else:
                                amp = "大幅"
                            market_label = f"市场{amp}看好主队"
                        else:
                            market_direction = "away"
                            if market_strength < self.MARKET_SIGNAL_SMALL:
                                amp = "小幅"
                            elif market_strength < self.MARKET_SIGNAL_LARGE:
                                amp = "中幅"
                            else:
                                amp = "大幅"
                            market_label = f"市场{amp}看好客队"
                        
                        # V4.10: 诱盘检测——当基本面与市场方向矛盾时，反转市场信任方向
                        inducement = features.get("fundamental_vs_market_divergence", 0) or 0
                        fund_score = features.get("fundamental_score", 0) or 0
                        if abs(inducement) > 0.02:
                            # 存在诱盘信号：市场方向与基本面矛盾 → 真实方向应追随基本面
                            fund_favors_home = fund_score > 0
                            fund_favors_away = fund_score < -0.05
                            if fund_favors_home and market_direction == "away":
                                market_direction = "home"
                                market_label += "（诱盘→反向看主）"
                            elif fund_favors_away and market_direction == "home":
                                market_direction = "away"
                                market_label += "（诱盘→反向看客）"
                        
                        # 模型方向
                        model_favors_home = corrected["home_prob"] > corrected["away_prob"]
                        model_favors_away = corrected["away_prob"] > corrected["home_prob"]
                        
                        # 模型与市场是否矛盾
                        model_vs_market = (model_favors_home and market_direction == "away") or (model_favors_away and market_direction == "home")
                        
                        if model_vs_market:
                            # 模型与市场方向矛盾 → 搏冷，权重由幅度决定
                            amp_factor = min(market_strength / self.MARKET_SIGNAL_AMPLIFY_MAX, 1.0)
                            shift = divergence_boost * (self.DIV_COUNTER_WEIGHT_BASE + self.DIV_COUNTER_WEIGHT_AMP * amp_factor)
                            if market_direction == "home":
                                corrected["home_prob"] += shift
                                corrected["away_prob"] = max(0.03, corrected["away_prob"] - shift)
                            else:
                                corrected["away_prob"] += shift
                                corrected["home_prob"] = max(0.03, corrected["home_prob"] - shift)
                            direction_label = f"模型-市场分歧→{market_label}"
                        elif market_direction == "neutral":
                            # 市场方向不明 → 分歧度高时压平局
                            corrected["draw_prob"] += divergence_boost * self.DIV_NEUTRAL_WEIGHT
                            if corrected["home_prob"] + corrected["away_prob"] > 0:
                                ratio_h = corrected["home_prob"] / (corrected["home_prob"] + corrected["away_prob"])
                                corrected["home_prob"] -= divergence_boost * self.DIV_NEUTRAL_WEIGHT * ratio_h
                                corrected["away_prob"] -= divergence_boost * self.DIV_NEUTRAL_WEIGHT * (1 - ratio_h)
                            direction_label = "市场方向不明→加平局"
                        else:
                            # 模型与市场方向一致 → 温和加强共识（幅度越大越确定）
                            amp_factor = min(market_strength / self.MARKET_SIGNAL_AMPLIFY_MAX, 1.0)
                            shift = divergence_boost * (self.DIV_AGREE_WEIGHT_BASE + self.DIV_AGREE_WEIGHT_AMP * amp_factor)
                            if model_favors_home:
                                corrected["home_prob"] += shift
                                corrected["away_prob"] = max(0.03, corrected["away_prob"] - shift)
                            else:
                                corrected["away_prob"] += shift
                                corrected["home_prob"] = max(0.03, corrected["home_prob"] - shift)
                            direction_label = f"模型-市场一致→{market_label}"
                
                for k in ["home_prob", "draw_prob", "away_prob"]:
                    corrected[k] = max(0.03, corrected[k])
        
        # ── Step 3: 庄家意图指数直接调整概率方向 ──
        intent = bookmaker_intent or 0
        intent_conflict = False
        if abs(intent) > 0.2:
            # intent > 0 = 看主队, intent < 0 = 看客队
            # 基准调整幅度 = |intent| * 0.08
            intent_shift = abs(intent) * 0.08
            
            # 冲突检测：intent方向 vs 市场隐含的主客偏向
            # 极度分歧时跳过——市场方向本身不可靠，intent作为独立信号更有价值
            if not is_extreme_divergence:
                intent_home_favor = intent > 0
                market_home_favor = market_home > market_away
                market_home_away_gap = abs(market_home - market_away)
                if intent_home_favor != market_home_favor and market_home_away_gap > 0.05:
                    intent_shift *= 0.5  # 冲突时减半
                    intent_conflict = True
            
            if intent > 0:
                corrected["home_prob"] += intent_shift
                corrected["away_prob"] = max(0.03, corrected["away_prob"] - intent_shift)
            else:
                corrected["away_prob"] += intent_shift
                corrected["home_prob"] = max(0.03, corrected["home_prob"] - intent_shift)
        
        # ── Step 4: H2H xG 与赔率变动一致性方向信号 ──
        # 用户核心逻辑：
        #   一致(+1): H2H xG和赔率指向同一方 → 强共识 → 加强该方向
        #   矛盾(-1): 赔率方向与H2H xG相悖 → 赔率可能是诱盘 → 信任H2H xG方向
        #   001例: H2H客xG碾压 + 客赔大降 → 一致 → 强化客队
        #   003例: H2H主xG稍优 + 客赔大降 → 矛盾 → 信任H2H，强化主队
        h2h_align = features.get("h2h_odds_alignment", 0) or 0
        if h2h_align != 0:
            # H2H xG方向
            h2h_xg_h = features.get("h2h_avg_home_xg", 0) or 0
            h2h_xg_a = features.get("h2h_avg_away_xg", 0) or 0
            favors_home = h2h_xg_h > h2h_xg_a + 0.03
            
            if h2h_align > 0:
                # 一致 → 强信号，加大幅度
                shift = 0.10
                if favors_home:
                    corrected["home_prob"] += shift
                    corrected["away_prob"] = max(0.03, corrected["away_prob"] - shift)
                else:
                    corrected["away_prob"] += shift
                    corrected["home_prob"] = max(0.03, corrected["home_prob"] - shift)
                h2h_signal_label = "H2H-赔率一致→强化共识"
            else:
                # 矛盾 → 赔率可能是诱盘，信任H2H xG
                shift = 0.06
                if favors_home:
                    corrected["home_prob"] += shift
                    corrected["away_prob"] = max(0.03, corrected["away_prob"] - shift)
                else:
                    corrected["away_prob"] += shift
                    corrected["home_prob"] = max(0.03, corrected["home_prob"] - shift)
                h2h_signal_label = "H2H-赔率矛盾→信任H2H"

        # 冷门修正详情
        reasons = []

        # ── Step 5: 盘口变化意图信号增强权重（V4.8 搏冷增强）──
        # 当盘口方向信号明确且一致时，大幅增加市场权重以对冲模型主流判断
        odds_direction = features.get("odds_consensus_direction", 0) or 0
        handicap_dir = features.get("handicap_consensus_direction", 0) or 0
        goal_drop = features.get("goal_line_drop_from_peak", 0) or 0
        over_move = features.get("over_odds_movement", 0) or 0
        
        # 检测盘口信号强度与一致性
        strong_bookmaker = abs(bookmaker_intent or 0) > 0.25
        strong_odds_dir = abs(odds_direction) > 0.5
        strong_goal_signal = goal_drop > 0.5 or abs(over_move) > 0.5
        extreme_goal_signal = goal_drop > 1.5 or abs(over_move) > 1.5
        
        if strong_bookmaker and strong_odds_dir:
            # 庄家意图 + 赔率共识方向一致 → 高度信任市场，加大搏冷权重
            same_dir = (bookmaker_intent > 0 and odds_direction > 0) or (bookmaker_intent < 0 and odds_direction < 0)
            if same_dir:
                # V4.8: 从20%提升到35%，增强搏冷属性
                extra_market = 0.35
                # 对有极端信号的，进一步拉大
                if extreme_goal_signal:
                    extra_market = 0.40
                reasons.append(f"搏冷信号共振（intent+共识），市场权重+{extra_market:.0%}")
                # 直接向市场方向增强修正
                if bookmaker_intent > 0:
                    corrected["home_prob"] += extra_market * 0.35
                    corrected["away_prob"] = max(0.03, corrected["away_prob"] - extra_market * 0.35)
                else:
                    corrected["away_prob"] += extra_market * 0.35
                    corrected["home_prob"] = max(0.03, corrected["home_prob"] - extra_market * 0.35)
            else:
                reasons.append("盘口信号方向冲突，保持中性")
        elif strong_goal_signal:
            # 大小球盘口信号强 → 增强市场权重
            extra_market = 0.20
            # 盘口回落 + 大球水位下跌 = 市场看小球 → 对极端方向做修正
            if goal_drop > 1.0 or over_move > 1.0:
                # V4.8: 从12%提升到25%，增强搏冷
                extra_market = 0.25
                max_prob = max(corrected["home_prob"], corrected["draw_prob"], corrected["away_prob"])
                if max_prob > 0.55:
                    excess = max_prob - 0.55
                    if corrected["home_prob"] == max_prob:
                        corrected["home_prob"] -= excess * 0.35
                        corrected["away_prob"] += excess * 0.25
                        corrected["draw_prob"] += excess * 0.1
                    elif corrected["away_prob"] == max_prob:
                        corrected["away_prob"] -= excess * 0.35
                        corrected["home_prob"] += excess * 0.25
                        corrected["draw_prob"] += excess * 0.1
                reasons.append(f"大小球盘口回落信号（goal_line↓{goal_drop:.1f}），搏冷权重+{extra_market:.0%}")
            else:
                reasons.append(f"大小球盘口信号，市场权重+{extra_market:.0%}")
        
        # ── 归一化 ──
        total = corrected["home_prob"] + corrected["draw_prob"] + corrected["away_prob"]
        for k in ["home_prob", "draw_prob", "away_prob"]:
            corrected[k] /= total

        # 冷门修正详情
        if abs(original_home - market_home) > 0.15 or abs(original_draw - market_draw) > 0.15:
            reasons.append("模型与市场方向分歧，已做加权修正")
        if divergence_boost > 0:
            reasons.append(f"分歧度增强：{direction_label}")
        if abs(intent) > 0.2:
            note = f"庄家意图信号({intent:+.2f})"
            if intent_conflict:
                note += "与市场方向冲突，权重减半"
            reasons.append(note + "已纳入概率修正")
        if h2h_align != 0:
            reasons.append(h2h_signal_label)
        
        corrected["cold_correction"] = {
            "model_original": {
                "home": round(original_home, 4),
                "draw": round(original_draw, 4),
                "away": round(original_away, 4),
            },
            "market_implied": {
                "home": round(market_home, 4),
                "draw": round(market_draw, 4),
                "away": round(market_away, 4),
            },
            "blend_ratio": {"model": MODEL_WEIGHT, "market": MARKET_WEIGHT},
            "divergence_direction": direction_label if divergence_boost > 0 else None,
            "intent_applied": round(intent, 2) if abs(intent) > 0.2 else 0,
            "reason": "；".join(reasons) if reasons else "模型与市场方向分歧：已做加权修正",
        }

        return corrected

    def _calc_confidence(self, result_a: dict, is_cold: bool) -> str:
        max_prob = max(result_a["home_prob"], result_a["draw_prob"], result_a["away_prob"])
        if is_cold or max_prob < 0.45:
            return "low"
        if max_prob > 0.60:
            return "high"
        return "medium"

    # ── V4.12: 数据质量检测 ──
    def _detect_data_quality(self, features_a: dict, features_b: dict, match) -> list[str]:
        """检测预测输入数据的质量缺陷，返回警告列表"""
        warnings = []

        # 1. 球队赛季数据不足
        home_played = int(features_a.get("home_games_played", 0) or 0)
        away_played = int(features_a.get("away_games_played", 0) or 0)
        if home_played < 5:
            home_name = match.home_team.name_zh if match and match.home_team else "主队"
            warnings.append(f"{home_name}赛季数据仅{home_played}场，球队实力评估可信度较低")
        if away_played < 5:
            away_name = match.away_team.name_zh if match and match.away_team else "客队"
            warnings.append(f"{away_name}赛季数据仅{away_played}场，球队实力评估可信度较低")

        # 2. 近期状态数据缺失（进球数据全部为 0）
        home_gf_avg = features_a.get("home_gf_avg_6", -1) or -1
        away_gf_avg = features_a.get("away_gf_avg_6", -1) or -1
        if home_gf_avg == 0 and away_gf_avg == 0:
            warnings.append("双方近期进球数据缺失，进球数预测可能偏差较大，建议结合市场盘口综合判断")

        # 3. 大小球盘口数据异常或缺失
        goal_line = features_b.get("goal_line_market", 0) or 0
        if goal_line < 0.5:
            warnings.append("大小球盘口数据缺失，进球数预测仅依赖球队基本面数据")
        elif goal_line < 1.5 or goal_line > 3.5:
            warnings.append(f"大小球盘口异常（{goal_line}），可能混入非标准盘口数据，已自动过滤")

        # 4. 赔率数据缺失
        bookmaker_count = features_b.get("bookmaker_count", 0) or 0
        if bookmaker_count < 3:
            warnings.append(f"赔率数据来源较少（仅{bookmaker_count}家博彩公司），市场参考价值有限")

        # 5. 赛季进球数据为零或异常
        home_goals_avg = features_a.get("home_goals_avg", 0) or 0
        away_goals_avg = features_a.get("away_goals_avg", 0) or 0
        if home_goals_avg == 0 and home_played > 0:
            warnings.append("主队赛季进球数据异常（场均进球为0），可能数据同步不完整")
        if away_goals_avg == 0 and away_played > 0:
            warnings.append("客队赛季进球数据异常（场均进球为0），可能数据同步不完整")

        return warnings

    def _generate_summary(self, result_a, result_b, is_cold) -> str:
        max_label = max(
            [("主胜", result_a["home_prob"]), ("平局", result_a["draw_prob"]), ("客胜", result_a["away_prob"])],
            key=lambda x: x[1]
        )
        summary = f"模型预测倾向{max_label[0]}（概率{max_label[1]*100:.1f}%），预期总进球{result_b['expected_goals']:.1f}球。"
        if is_cold:
            summary += " 注意：本场被标记为冷门预警赛事，建议谨慎参考。"
        return summary

    # ── 模型推理逻辑生成 ──
    def _generate_key_factors(self, features_a: dict, features_b: dict, result_a: dict, result_b: dict, is_cold: bool) -> dict:
        """生成模型A+B的逻辑支点和佐证因素，包括特征分析、推理链路、关键因子的结构化数据"""
        result = {
            "model_a": self._analyze_model_a(features_a, result_a, is_cold),
            "model_b": self._analyze_model_b(features_b, result_b),
            "joint_analysis": self._joint_analysis(result_a, result_b),
        }
        return result

    def _analyze_model_a(self, features: dict, result_a: dict, is_cold: bool) -> dict:
        """分析模型A的推理逻辑：基于特征方向和重要度"""
        model = self.model_a.model_wl
        feature_names = model.feature_name_ if model and hasattr(model, "feature_name_") else []
        importances = {}

        # 获取 LightGBM 特征重要性 (gain)
        if model and len(feature_names) > 0:
            raw_imp = model.booster_.feature_importance(importance_type="gain")
            raw_imp = np.maximum(raw_imp, 0)
            total = raw_imp.sum()
            if total > 0:
                importances = {feature_names[i]: raw_imp[i] / total for i in range(len(feature_names))}

        # 确定预测方向
        probs = {"主胜": result_a["home_prob"], "平局": result_a["draw_prob"], "客胜": result_a["away_prob"]}
        pred_direction = max(probs, key=probs.get)
        pred_prob = probs[pred_direction]

        # 提取支撑因素：分析每个特征值及其方向
        supporting: list[dict] = []
        neutral: list[dict] = []

        for fname, value in features.items():
            cn_name = FEATURE_NAME_CN.get(fname, fname)
            direction = FEATURE_DIRECTION.get(fname)
            importance = importances.get(fname, 0)

            if importance < 0.003:
                continue  # 跳过无足轻重的特征

            if direction == "home":
                if pred_direction == "主胜" and value > 0:
                    tag = "support"
                    impact = "利好主胜"
                elif pred_direction == "客胜" and value > 0:
                    tag = "contradict"
                    impact = "不利客胜"
                else:
                    tag = "neutral"
                    impact = "中性"
            elif direction == "away":
                if pred_direction == "客胜" and value > 0:
                    tag = "support"
                    impact = "利好客胜"
                elif pred_direction == "主胜" and value > 0:
                    tag = "contradict"
                    impact = "不利主胜"
                else:
                    tag = "neutral"
                    impact = "中性"
            elif direction == "cold":
                tag = "support" if is_cold else "neutral"
                impact = "赔率分歧大"
            else:
                continue

            item = {
                "feature": cn_name,
                "value": self._format_feature_value(fname, value),
                "impact": impact,
                "importance": round(importance * 100, 1),
            }
            if tag == "support":
                supporting.append(item)
            elif tag == "neutral":
                neutral.append(item)

        # 按重要性排序
        supporting.sort(key=lambda x: x["importance"], reverse=True)
        neutral.sort(key=lambda x: x["importance"], reverse=True)

        # 取 Top 因素
        top_support = supporting[:6]
        top_neutral = neutral[:3]

        # 生成推理文本
        if top_support:
            support_items = [f"{s['feature']}({s['value']})" for s in top_support[:4]]
            reasoning = f"核心支撑因素：{'；'.join(support_items)}。综合判定{pred_direction}概率{pred_prob*100:.1f}%。"
        else:
            reasoning = f"模型综合各维特征后判定{pred_direction}概率{pred_prob*100:.1f}%，各项特征未呈现明显倾向性。"

        return {
            "method": "LightGBM 多任务分类",
            "pred_direction": pred_direction,
            "pred_prob": round(pred_prob * 100, 1),
            "reasoning": reasoning,
            "top_features": top_support,
            "other_features": top_neutral,
            "full_probs": {
                "主胜": round(result_a["home_prob"] * 100, 1),
                "平局": round(result_a["draw_prob"] * 100, 1),
                "客胜": round(result_a["away_prob"] * 100, 1),
            },
        }

    def _analyze_model_b(self, features: dict, result_b: dict) -> dict:
        """分析模型B的推理逻辑：基于 Poisson 回归系数"""
        model = self.model_b.model
        feature_names = self.model_b._feature_names or []
        coefficients = {}

        # 获取 Poisson 回归系数
        if model and feature_names:
            coefs = model.coef_
            for i, fname in enumerate(feature_names):
                if i < len(coefs):
                    coefficients[fname] = coefs[i]

        lambda_val = result_b["expected_goals"]
        over_prob = result_b["over_2_5_prob"]

        # 找出对进球预测贡献最大的特征
        positive: list[dict] = []
        negative: list[dict] = []

        for fname in GOALS_RELEVANT_FEATURES:
            if fname not in features:
                continue
            cn_name = FEATURE_NAME_CN.get(fname, fname)
            value = features.get(fname, 0)
            coef = coefficients.get(fname, 0)
            contribution = coef * value

            # V4.11: 降低阈值（原0.02 → 0.005），让负系数特征有机会出现
            # 同时扩大追踪范围：纳入更多对进球有影响的特征
            if contribution > 0.005:
                positive.append({
                    "feature": cn_name,
                    "value": self._format_feature_value(fname, value),
                    "contribution": round(abs(contribution), 4),
                })
            elif contribution < -0.005:
                negative.append({
                    "feature": cn_name,
                    "value": self._format_feature_value(fname, value),
                    "contribution": round(abs(contribution), 4),
                })

        positive.sort(key=lambda x: x["contribution"], reverse=True)

        # 生成推理文本
        if positive:
            pos_items = [f"{p['feature']}({p['value']})" for p in positive[:3]]
            predict_desc = "大球" if over_prob > 0.5 else "小球"
            reasoning = f"进球佐证因素：{'；'.join(pos_items)}。Poisson回归估计λ={lambda_val:.1f}球，倾向{predict_desc}(大2.5概率{over_prob*100:.0f}%)。"
        else:
            reasoning = f"Poisson回归估计预期总进球{lambda_val:.1f}球，大2.5概率{over_prob*100:.0f}%，进球信号未呈现明显偏大倾向。"

        return {
            "method": "Poisson 回归",
            "lambda": round(lambda_val, 2),
            "over_2_5_prob": round(over_prob * 100, 0),
            "reasoning": reasoning,
            "push_factors": positive[:4],
            "pull_factors": negative[:3],
            "goal_distribution": [round(p * 100, 1) for p in result_b["goal_distribution"]],
        }

    def _joint_analysis(self, result_a: dict, result_b: dict) -> str:
        """模型A+B联合分析"""
        probs = {"主胜": result_a["home_prob"], "平局": result_a["draw_prob"], "客胜": result_a["away_prob"]}
        direction = max(probs, key=probs.get)
        lambda_val = result_b["expected_goals"]
        over_prob = result_b["over_2_5_prob"]

        if direction == "主胜":
            score_hint = f"{int(lambda_val * 0.55 + 0.5)}:{int(lambda_val * 0.45)}" if lambda_val > 1.5 else "1:0"
        elif direction == "客胜":
            score_hint = f"{int(lambda_val * 0.45)}:{int(lambda_val * 0.55 + 0.5)}" if lambda_val > 1.5 else "0:1"
        else:
            score_hint = f"{int(lambda_val / 2)}:{int(lambda_val / 2)}" if lambda_val > 1 else "1:1"

        return (
            f"模型A倾向{direction}（{probs[direction]*100:.1f}%），"
            f"模型B预期总进球{lambda_val:.1f}球（大2.5概率{over_prob*100:.0f}%），"
            f"A+B联合推导参考比分倾向{score_hint}。"
        )

    @staticmethod
    def _format_feature_value(fname: str, value: float) -> str:
        """将特征值格式化为人类可读的字符串"""
        if fname in ("home_win_rate", "home_draw_rate", "home_home_win_rate",
                     "home_clean_sheet_rate", "home_points_per_game",
                     "away_win_rate", "away_goals_avg", "away_away_win_rate",
                     "away_points_per_game", "win_rate_diff", "goals_avg_diff",
                     "home_form_pts_6", "home_form_pts_10", "home_gf_avg_6",
                     "away_form_pts_6", "away_form_pts_10", "away_gf_avg_6",
                     "home_home_form_pts", "home_away_form_pts",
                     "away_home_form_pts", "away_away_form_pts",
                     "odds_implied_home_change", "odds_market_home_prob",
                     "odds_market_draw_prob", "odds_market_away_prob"):
            return f"{value * 100:.1f}%"
        elif fname in ("home_xG", "home_xGA", "away_xG", "away_xGA",
                       "home_goals_avg", "home_goals_against_avg",
                       "away_goals_against_avg", "h2h_avg_goals",
                       "expected_goals", "home_ga_avg_6", "away_ga_avg_6",
                       "points_per_game_diff"):
            return f"{value:.2f}"
        elif fname in ("points_diff", "h2h_match_count", "h2h_home_wins",
                       "h2h_draws", "h2h_away_wins", "h2h_total_goals",
                       "home_games_played", "away_games_played",
                       "home_injuries", "away_injuries"):
            return f"{int(value)}"
        elif fname in ("handicap_line", "odds_home_initial", "odds_draw_initial",
                       "odds_away_initial", "odds_home_current", "odds_draw_current",
                       "odds_away_current", "draw_odds_current", "odds_std_home",
                       "odds_dispersity", "deviation_abs_max"):
            return f"{value:.2f}"
        elif fname in ("odds_movement_home", "odds_movement_draw", "odds_movement_away",
                       "odds_implied_home_change", "deviation_home", "deviation_draw",
                       "deviation_away"):
            return f"{value:+.3f}" if abs(value) < 1 else f"{value:+.2f}"
        elif fname == "home_motivation" or fname == "away_motivation" or fname == "motivation_diff":
            return f"{value:.2f}"
        elif fname in ("home_form_trend", "away_form_trend"):
            return f"{value:+.2f}"
        return f"{value}"

    def _empty_result(self) -> dict:
        return {
            "home_prob": 0.33, "draw_prob": 0.34, "away_prob": 0.33,
            "handicap_home_prob": 0.33, "handicap_draw_prob": 0.34, "handicap_away_prob": 0.33,
            "expected_goals": 2.5, "goal_distribution": [0.08,0.20,0.25,0.21,0.26],
            "over_2_5_prob": 0.50, "score_top5_json": [],
            "is_cold_match": True, "confidence_level": "low",
            "data_quality": ["特征提取失败，预测数据不可用"],
            "summary_text": "数据不足，无法生成预测报告。",
            "key_factors": "{}",
        }
