"""方向+置信度门控模型 —— 超脱 λ→Poisson→SNAP 猜整数的体系外进球预测

核心思想
--------
不再预测"具体进球数"（SNAP Top2 理论上限 ≈55%，与盲猜 {2,3} 47% 无异），
而是直接读 TTG 总进球市场（竞彩官方进球数盘口）定价判断"大球/小球方向"，
并用市场隐含概率的置信度做选择性下注：低置信度直接跳过。

市场隐含概率单调可信（置信度越高，方向命中率越高），无需回归、无需调参。

验证依据（180天/1777场，backend/tools/_diag_ttg_val4.py）
--------------------------------------------------------
置信度分桶（P大 = 市场隐含大球概率，2.5 线）：
    ≥0.65       判大  66.5%   (N=394)
    0.58-0.65   判大  60.2%   (N=430)
    0.52-0.58   判大  49.7%   (N=366)   ← 噪声带
    0.48-0.52   跳过  (N=239)
    0.42-0.48   判小  58.7%   (N=264)
    0.35-0.42   判小  66.2%   (N=77)
    <0.35       判小  71.4%   (N=7)

门控下注命中率（动态档位）：
    ≥0.58 / ≤0.42 → 63.5%   (覆盖 51.1%)
    ≥0.62 / ≤0.38 → 65.7%   (覆盖 31.2%)
    ≥0.65 / ≤0.35 → 66.6%   (覆盖 22.6%)

输出形态（用户决策：方向为主 + 整数补充）
    - 方向: over(大球) / under(小球) / skip(跳过)
    - 置信度: max(P大, P小)
    - 整数补充: 隐含概率最高的 2 个进球数（仅参考展示，不用于下注）
"""
import json
from typing import Optional

# 动态档位：{tier: {"over": P大判大阈值, "under": P大判小阈值, "label": 展示名}}
# 判定规则：P大 >= over → 大球；P大 <= under → 小球；否则 skip
TIERS = {
    "full":     {"over": 0.50, "under": 0.50, "label": "全量无门控"},
    "noise":    {"over": 0.52, "under": 0.48, "label": "去噪声带"},
    "loose":    {"over": 0.58, "under": 0.42, "label": "宽松 ≥.58/≤.42"},
    "standard": {"over": 0.62, "under": 0.38, "label": "标准 ≥.62/≤.38"},
    "strict":   {"over": 0.65, "under": 0.35, "label": "严格 ≥.65/≤.35"},
}
DEFAULT_TIER = "standard"

# 置信度分桶展示标签
_BUCKETS = (
    (0.65, None, "≥0.65"),
    (0.58, 0.65, "0.58-0.65"),
    (0.52, 0.58, "0.52-0.58"),
    (0.48, 0.52, "0.48-0.52"),
    (0.42, 0.48, "0.42-0.48"),
    (0.35, 0.42, "0.35-0.42"),
    (None, 0.35, "<0.35"),
)


def _parse_ttg_odds(ttg_odds_json) -> Optional[dict]:
    """解析 TTG 赔率（支持 dict 或 JSON 字符串），返回 {k: odds}；无效返回 None"""
    if ttg_odds_json is None:
        return None
    data = ttg_odds_json
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    out = {}
    for k, v in data.items():
        try:
            odds = float(v)
        except (ValueError, TypeError):
            continue
        if odds > 0:
            out[int(k)] = odds
    return out or None


def _implied(odds_map: dict, goal_line: float = 2.5) -> Optional[tuple]:
    """由赔率计算归一化隐含概率分布，返回 (probs: {k: p}, p_big)"""
    inv = {k: 1.0 / odds for k, odds in odds_map.items()}
    total = sum(inv.values())
    if total <= 0:
        return None
    probs = {k: v / total for k, v in inv.items()}
    p_big = sum(p for k, p in probs.items() if k > goal_line)
    return probs, max(0.0, min(1.0, p_big))


def _bucket_label(p_big: float) -> str:
    for hi, lo, label in _BUCKETS:
        if hi is not None and p_big >= hi:
            return label
        if lo is not None and p_big < lo:
            return label
    return "0.48-0.52"


def _verdict(p_big: float, tier: str) -> str:
    t = TIERS.get(tier, TIERS[DEFAULT_TIER])
    if p_big >= t["over"]:
        return "over"
    if p_big <= t["under"]:
        return "under"
    return "skip"


def ou_direction_from_ttg(ttg_odds_json, tier: str = DEFAULT_TIER, goal_line: float = 2.5) -> Optional[dict]:
    """单档位入口：由 TTG 赔率计算大小球方向 + 置信度 + 门控判定。

    返回 None = 无有效 TTG 赔率（无信号）。返回 dict:
      p_big / p_small   市场隐含大小球概率
      direction         "over" | "under" | "skip"
      confidence        max(P大, P小)
      bucket            置信度分桶标签（如 "≥0.65"）
      top2_ints         隐含概率最高的 2 个整数进球数（补充参考）
      distribution      隐含分布 {k: p}
      tier              实际档位
    """
    odds_map = _parse_ttg_odds(ttg_odds_json)
    if not odds_map:
        return None
    parsed = _implied(odds_map, goal_line)
    if parsed is None:
        return None
    probs, p_big = parsed
    p_small = 1.0 - p_big
    if tier not in TIERS:
        tier = DEFAULT_TIER
    top_ints = sorted(probs, key=lambda k: probs[k], reverse=True)[:2]
    return {
        "p_big": round(p_big, 4),
        "p_small": round(p_small, 4),
        "goal_line": goal_line,
        "direction": _verdict(p_big, tier),
        "confidence": round(max(p_big, p_small), 4),
        "bucket": _bucket_label(p_big),
        "top2_ints": [int(x) for x in top_ints],
        "distribution": {int(k): round(p, 4) for k, p in probs.items()},
        "tier": tier,
    }


def ou_direction_all_tiers(ttg_odds_json, goal_line: float = 2.5) -> Optional[dict]:
    """全档位入口：共享一次分布解析，返回 {tier: ou_direction dict}；无信号返回 None。

    供 summary 一次性产出各档位命中率，避免逐档重复解析。
    """
    odds_map = _parse_ttg_odds(ttg_odds_json)
    if not odds_map:
        return None
    parsed = _implied(odds_map, goal_line)
    if parsed is None:
        return None
    probs, p_big = parsed
    p_small = 1.0 - p_big
    top_ints = sorted(probs, key=lambda k: probs[k], reverse=True)[:2]
    out = {}
    for tier in TIERS:
        out[tier] = {
            "p_big": round(p_big, 4),
            "p_small": round(p_small, 4),
            "goal_line": goal_line,
            "direction": _verdict(p_big, tier),
            "confidence": round(max(p_big, p_small), 4),
            "bucket": _bucket_label(p_big),
            "top2_ints": [int(x) for x in top_ints],
            "tier": tier,
        }
    return out
