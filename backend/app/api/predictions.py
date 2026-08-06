"""预测相关 API"""
import json
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta
from app.db.database import get_db
from app.db.models import Prediction, Match, League
from app.predictor.pipeline import PredictionPipeline, FEATURE_NAME_CN, FEATURE_DIRECTION

router = APIRouter(prefix="/api/predictions", tags=["predictions"])

# ── 工具函数 ──

def _parse_key_factors(raw):
    """安全解析 key_factors JSON"""
    if not raw:
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None

def _date_range(day_str: str):
    """将 YYYY-MM-DD 转换为竞彩比赛日范围 [当日12:00, 次日12:00)"""
    d = datetime.strptime(day_str, "%Y-%m-%d")
    start = d.replace(hour=12, minute=0, second=0)
    end = start + timedelta(hours=24)
    return start, end

def _determine_actual_result(home_score, away_score):
    """根据比分确定实际赛果方向"""
    if home_score is None or away_score is None:
        return None
    if home_score > away_score:
        return "主胜"
    elif home_score < away_score:
        return "客胜"
    else:
        return "平局"

def _analyze_error(pred_direction: str, actual_result: str, key_factors: dict | None):
    """分析预测错误根因：基于模型A特征分析"""
    if not key_factors or not key_factors.get("model_a"):
        return None

    model_a = key_factors["model_a"]
    top_features = model_a.get("top_features", [])
    other_features = model_a.get("other_features", [])

    # 区分：哪些特征推向了错误方向（misleading），哪些本应被更多加权（missed_signals）
    misleading = []
    missed_signals = []

    for f in top_features:
        # 支持预测方向的特征，但实际结果相反 → 误导
        misleading.append({
            "feature": f["feature"],
            "value": f["value"],
            "impact": f["impact"],
            "importance": f.get("importance", 0),
        })

    for f in other_features:
        # 这些是被模型忽略或低权重的特征，看是否指向实际结果
        missed_signals.append({
            "feature": f["feature"],
            "value": f["value"],
            "impact": f.get("impact", "中性"),
            "importance": f.get("importance", 0),
        })

    # 按重要度排序
    misleading.sort(key=lambda x: x["importance"], reverse=True)

    # 生成根因摘要
    top_misleading_names = [m["feature"] for m in misleading[:3]]
    if top_misleading_names:
        summary = f"模型过度依赖{'、'.join(top_misleading_names)}等特征，判定{pred_direction}，但实际赛果为{actual_result}"
    else:
        summary = f"模型判定{pred_direction}，但实际赛果为{actual_result}，各项特征未呈现明显倾向"

    return {
        "misleading_features": misleading[:5],
        "missed_signals": missed_signals[:5],
        "summary": summary,
    }

def _get_date_label(day_str: str) -> str:
    """日期 → 中文标签"""
    d = datetime.strptime(day_str, "%Y-%m-%d")
    today = datetime.now()
    if d.date() == today.date():
        return f"{day_str}（今天）"
    elif d.date() == today.date() - timedelta(days=1):
        return f"{day_str}（昨天）"
    return day_str

def _get_hcp_actual(home_score, away_score, handicap_line):
    """计算让球后的实际赛果方向"""
    if home_score is None or away_score is None:
        return None
    adj = home_score + (handicap_line or 0)
    if adj > away_score:
        return "home"
    elif adj < away_score:
        return "away"
    else:
        return "draw"


@router.get("/{match_id}")
async def get_prediction(match_id: int, db: AsyncSession = Depends(get_db)):
    """单场预测完整数据"""
    result = await db.execute(select(Prediction).where(Prediction.match_id == match_id))
    pred = result.scalar_one_or_none()
    if not pred:
        return {"data": None, "message": "预测数据尚未生成"}

    # 解析 key_factors JSON 字符串
    key_factors = None
    if pred.key_factors:
        try:
            key_factors = json.loads(pred.key_factors) if isinstance(pred.key_factors, str) else pred.key_factors
        except (json.JSONDecodeError, TypeError):
            key_factors = None

    # 解析 risk_warning JSON 字符串
    risk_warning = None
    if pred.risk_warning:
        try:
            risk_warning = json.loads(pred.risk_warning) if isinstance(pred.risk_warning, str) else pred.risk_warning
        except (json.JSONDecodeError, TypeError):
            risk_warning = None

    # Model C 计算明细（按需获取）
    model_c_detail = None
    try:
        from app.predictor.models.model_c import ModelC
        from app.predictor.features_b import FeatureEngineerB
        from app.db.models import Match as MatchModel
        from sqlalchemy.orm import joinedload

        match_result = await db.execute(
            select(MatchModel).options(joinedload(MatchModel.league)).where(MatchModel.id == match_id)
        )
        match = match_result.unique().scalar_one_or_none()
        if match:
            league_name = match.league.name_zh if match.league else None
            feat_engine = FeatureEngineerB(db)
            features_df = await feat_engine.extract_features(match_id)
            if not features_df.empty:
                features = features_df.iloc[0].to_dict()
                result_c = ModelC().predict(features, league_name)
                model_c_detail = result_c.get("detail")
    except Exception:
        pass  # 明细获取失败不影响主数据返回

    return {"data": {
        "home_prob": pred.home_prob, "draw_prob": pred.draw_prob, "away_prob": pred.away_prob,
        "handicap_home_prob": pred.handicap_home_prob, "handicap_draw_prob": pred.handicap_draw_prob, "handicap_away_prob": pred.handicap_away_prob,
        "expected_goals": pred.expected_goals, "over_2_5_prob": pred.over_2_5_prob,
        "goal_distribution": pred.goal_distribution,
        "expected_goals_c": pred.expected_goals_c, "snap_top2_c": pred.snap_top2_c,
        "model_c_detail": model_c_detail,
        "score_top5_json": pred.score_top5_json,
        "confidence_level": pred.confidence_level, "is_cold_match": pred.is_cold_match,
        "cold_correction": pred.cold_correction,
        "summary_text": pred.summary_text, "key_factors": key_factors, "risk_warning": risk_warning,
        "model_version": pred.model_version, "created_at": str(pred.created_at),
        # 实际赛果（比赛结束后回写）
        "actual_home_score": pred.actual_home_score, "actual_away_score": pred.actual_away_score,
        "actual_total_goals": pred.actual_total_goals, "actual_score": pred.actual_score,
        "result_spf": pred.result_spf, "result_hcp": pred.result_hcp,
        "result_goals": pred.result_goals, "result_score": pred.result_score,
    }}


@router.get("/review/summary")
async def get_review_summary(days: int = Query(30), db: AsyncSession = Depends(get_db)):
    """复盘统计概览"""
    return {
        "data": {
            "total_predictions": 0, "wl_accuracy": 0.0, "wl_accuracy_last_week": 0.0,
            "handicap_accuracy": 0.0, "goal_accuracy": 0.0, "score_top3_accuracy": 0.0,
        }
    }


@router.get("/review/trend")
async def get_accuracy_trend(days: int = Query(30), db: AsyncSession = Depends(get_db)):
    """每日准确率趋势"""
    return {"data": []}


@router.get("/pnl")
async def get_pnl(days: int = Query(30), db: AsyncSession = Depends(get_db)):
    """盈亏模拟"""
    return {"data": {"total_pnl": 0.0, "roi": 0.0, "series": []}}


@router.get("/review/daily")
async def get_daily_review(
    date: str = Query(None, description="日期 YYYY-MM-DD，不传默认昨天"),
    db: AsyncSession = Depends(get_db),
):
    """每日复盘详情：预测 vs 赛果对比 + 模型推理链路 + 错误根因分析"""
    from sqlalchemy.orm import joinedload

    if not date:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    start, end = _date_range(date)

    result = await db.execute(
        select(Prediction)
        .options(
            joinedload(Prediction.match).joinedload(Match.home_team),
            joinedload(Prediction.match).joinedload(Match.away_team),
            joinedload(Prediction.match).joinedload(Match.league),
        )
        .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
        .order_by(Prediction.kickoff_time)
    )
    predictions = result.unique().scalars().all()

    matches_data = []
    spf_hit = 0
    spf_miss = 0
    spf_unsettled = 0
    hcp_hit = 0
    hcp_miss = 0
    goals_hit = 0
    goals_miss = 0

    for pred in predictions:
        match = pred.match
        if match:
            home_name = match.home_team.name_zh if match.home_team else (match.home_team_name or "")
            away_name = match.away_team.name_zh if match.away_team else (match.away_team_name or "")
            league_name = match.league.name_zh if match.league else ""
        else:
            home_name = away_name = league_name = ""

        # 实际赛果
        actual_result = _determine_actual_result(pred.actual_home_score, pred.actual_away_score)

        # 预测方向
        spf_probs = {"主胜": pred.home_prob or 0, "平局": pred.draw_prob or 0, "客胜": pred.away_prob or 0}
        pred_direction = max(spf_probs, key=spf_probs.get)

        # 命中状态——基于当前预测概率动态判定，而非 DB 中可能过期的 result_spf
        is_unsettled = actual_result is None
        if is_unsettled:
            is_hit = False
            is_miss = False
        else:
            is_hit = pred_direction == actual_result
            is_miss = not is_hit

        if is_hit:
            spf_hit += 1
        elif is_miss:
            spf_miss += 1
        else:
            spf_unsettled += 1

        # HCP / Goals 命中基于当前预测动态计算
        if not is_unsettled:
            # 让球判定
            hcp_home = pred.handicap_home_prob or 0
            hcp_draw = pred.handicap_draw_prob or 0
            hcp_away = pred.handicap_away_prob or 0
            hcp_pred = max({"home": hcp_home, "draw": hcp_draw, "away": hcp_away}, key={"home": hcp_home, "draw": hcp_draw, "away": hcp_away}.get)
            hcp_actual = _get_hcp_actual(pred.actual_home_score, pred.actual_away_score, match.handicap_line if match else 0)
            if hcp_pred == hcp_actual:
                hcp_hit += 1
            else:
                hcp_miss += 1

            # V4.12 SNAP: λ小数<0.10降级、>0.90升级，2 closest to effective，4=4+
            actual_total = (pred.actual_home_score or 0) + (pred.actual_away_score or 0)
            expected_goals = pred.expected_goals or 0
            if expected_goals > 0 and actual_total >= 0:
                import math
                SNAP_DOWN = 0.10   # 小数低于此值向下取整（如 3.03→取[2,3]）
                SNAP_UP   = 0.90   # 小数高于此值向上取整（如 3.93→取[3,4]）
                eg_clean = round(expected_goals, 10)  # 去浮点噪声，确保 2.10 不被误判为 2.0999
                frac = eg_clean - math.floor(eg_clean)
                if frac < SNAP_DOWN:
                    effective = math.floor(eg_clean)
                elif frac > SNAP_UP:
                    effective = math.ceil(eg_clean)
                else:
                    effective = expected_goals
                dists = sorted([(abs(effective - i), i) for i in range(5)])
                top2 = {dists[0][1], dists[1][1]}
                act_capped = min(actual_total, 4)
                if act_capped in top2:
                    goals_hit += 1
                else:
                    goals_miss += 1

        # 解析 key_factors
        kf = _parse_key_factors(pred.key_factors)

        # 错误分析（仅对预测错误的比赛）
        error_analysis = None
        if is_miss and actual_result and kf:
            error_analysis = _analyze_error(pred_direction, actual_result, kf)

        matches_data.append({
            "match_id": pred.match_id,
            "home_team": home_name,
            "away_team": away_name,
            "league_name": league_name,
            "kickoff_time": str(pred.kickoff_time),
            # 预测
            "home_prob": pred.home_prob,
            "draw_prob": pred.draw_prob,
            "away_prob": pred.away_prob,
            "pred_direction": pred_direction,
            "confidence_level": pred.confidence_level,
            "is_cold_match": pred.is_cold_match,
            "expected_goals": pred.expected_goals,
            # 实际
            "actual_score": pred.actual_score,
            "actual_result": actual_result,
            "actual_home_score": pred.actual_home_score,
            "actual_away_score": pred.actual_away_score,
            # 命中状态
            "result_spf": pred.result_spf,
            "result_hcp": pred.result_hcp,
            "result_goals": pred.result_goals,
            "is_hit": is_hit,
            "is_miss": is_miss,
            "is_unsettled": is_unsettled,
            # 模型推理
            "key_factors": kf,
            # 错误分析
            "error_analysis": error_analysis,
        })

    settled = spf_hit + spf_miss
    spf_accuracy = round(spf_hit / settled * 100, 1) if settled > 0 else 0
    hcp_accuracy = round(hcp_hit / (hcp_hit + hcp_miss) * 100, 1) if (hcp_hit + hcp_miss) > 0 else 0
    goals_accuracy = round(goals_hit / (goals_hit + goals_miss) * 100, 1) if (goals_hit + goals_miss) > 0 else 0

    # 错误模式聚合
    error_patterns = _aggregate_error_patterns(matches_data)

    return {
        "data": {
            "date": date,
            "date_label": _get_date_label(date),
            "total": len(predictions),
            "settled": settled,
            "unsettled": spf_unsettled,
            "spf_hit": spf_hit,
            "spf_miss": spf_miss,
            "spf_accuracy": spf_accuracy,
            "hcp_hit": hcp_hit,
            "hcp_miss": hcp_miss,
            "hcp_accuracy": hcp_accuracy,
            "goals_hit": goals_hit,
            "goals_miss": goals_miss,
            "goals_accuracy": goals_accuracy,
            "matches": matches_data,
            "error_patterns": error_patterns,
        }
    }


def _aggregate_error_patterns(matches_data: list) -> list:
    """聚合当天错误模式：找出整体倾向"""
    patterns = []

    # 统计预测方向 vs 实际方向的偏差
    direction_errors = {}  # key: "主胜→客胜", value: count
    misleading_feature_counter = {}  # 被误导的特征统计

    for m in matches_data:
        if not m["is_miss"] or not m.get("error_analysis"):
            continue

        pred = m["pred_direction"]
        actual = m["actual_result"]
        key = f"{pred}→{actual}"
        direction_errors[key] = direction_errors.get(key, 0) + 1

        # 统计误导特征
        ea = m["error_analysis"]
        for mf in ea.get("misleading_features", []):
            fname = mf["feature"]
            if fname not in misleading_feature_counter:
                misleading_feature_counter[fname] = {"count": 0, "importance_sum": 0}
            misleading_feature_counter[fname]["count"] += 1
            misleading_feature_counter[fname]["importance_sum"] += mf.get("importance", 0)

    # 方向偏差 Top
    if direction_errors:
        sorted_de = sorted(direction_errors.items(), key=lambda x: x[1], reverse=True)
        desc_parts = [f"{k}({v}次)" for k, v in sorted_de[:3]]
        patterns.append({
            "type": "方向偏差",
            "description": f"当日预测错误中，最常见的偏差模式为：{'；'.join(desc_parts)}",
            "details": sorted_de[:5],
        })

    # 误导特征 Top
    if misleading_feature_counter:
        sorted_mf = sorted(misleading_feature_counter.items(),
                          key=lambda x: x[1]["count"] * 10 + x[1]["importance_sum"],
                          reverse=True)
        mf_parts = [f"{k}({v['count']}次)" for k, v in sorted_mf[:5]]
        patterns.append({
            "type": "误导特征",
            "description": f"当日错误预测中，以下特征被频繁误判：{'、'.join(mf_parts)}",
            "details": sorted_mf[:8],
        })

    return patterns

@router.get("/models/versions")
async def get_model_versions(db: AsyncSession = Depends(get_db)):
    """模型版本对比"""
    result = await db.execute(
        select(Prediction.model_version, func.count(Prediction.id).label("total"))
        .group_by(Prediction.model_version).order_by(Prediction.model_version.desc())
    )
    versions = result.all()
    return {"data": [{"version": v[0], "total_predictions": v[1]} for v in versions]}
