# -*- coding: utf-8 -*-
"""半平/半全场 两级筛选规则：R1 首选(半平) / R2 次选(胜胜+负负双选)。

背景与口径
----------
- R1 目标：半场平（半场比分相等）。
- R2 目标：强热门场按 fav 方向单选 半全场 胜胜/负负（fav主→胜胜HH、fav客→负负AA，1注）；
  作为 R1 当日无候选时的次选（HH/AA 与半平天然互补）。
- 特征全部为赛前可得（联赛 + HAD/让球 赔率隐含）：
    league : 联赛中文名（对齐 leagues.name_zh）
    home_w : HAD 主胜隐含 / (主胜隐含+客胜隐含)，0~1
    fav    : 让球线方向 "home"/"away"
    fav_ip : fav 方向的 HAD 隐含概率（去水后归一）
- 组A（高半平联赛，由近2月 619 场已结算样本归纳）：韩K/西甲/意甲/日职联/美职联/欧冠/葡超/巴西杯。

规则语义
--------
- R1（首选）：league ∈ 组A 且 0.53 <= home_w <= 0.63
    → 半平命中 32/46 = 69.6%
- R2（次选）：仅当某日 R1 无任何候选时启用；fav_ip >= 0.60（fav 方向已知）
    → fav侧单选命中 87/167 = 52.1%（HH∪AA 双选口径 100/167 = 59.9%）
- 两级合计（07-01~09-08 已结算 619 场样本）：见 tools/_halfdraw_2level_daily.txt。
  注：以上为样本内统计，落地前建议做滚动前瞻验证。

使用
----
    from app.halfdraw_rules import select_for_day, rule_code, R1, R2
    matches = [{"league": "韩K", "home_w": 0.58, "fav_ip": 0.55, "fav": "home"}, ...]
    code, picks = select_for_day(matches)   # code: RULE_TOP / RULE_FALLBACK / None
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 高半平联赛组（组A）
LEAGUES_GROUP_A = frozenset({
    "韩K", "西甲", "意甲", "日职联", "美职联", "欧冠", "葡超", "巴西杯",
})

# R1: home_w 区间（主队中等优势）
HW_LO, HW_HI = 0.53, 0.63
# R2: fav 方向隐含强度下限（HH/AA 双选次选）
FAV_IP_MIN = 0.60

RULE_TOP = "R1"
RULE_FALLBACK = "R2"

R1 = {
    "code": RULE_TOP,
    "name": "半平首选",
    "desc": "联赛∈组A 且 home_w∈[0.53,0.63]",
    "hit": "半平 32/46 = 69.6%",
}
R2 = {
    "code": RULE_FALLBACK,
    "name": "胜胜/负负次选(fav侧单选)",
    "desc": "fav_ip>=0.60 → 按 fav 方向单选 胜胜或负负(1注)；仅 R1 当日无货时启用",
    "hit": "fav侧 87/167 = 52.1%",
}


def is_r1(match: Dict) -> bool:
    """R1 首选：高半平联赛 + 主队中等优势（目标=半场平）。"""
    league = match.get("league")
    home_w = match.get("home_w")
    if league not in LEAGUES_GROUP_A or home_w is None:
        return False
    return HW_LO <= home_w <= HW_HI


def is_r2(match: Dict) -> bool:
    """R2 次选：强热门(fav_ip>=0.60 且 fav 方向已知)，目标=fav侧 胜胜/负负 单选。"""
    fav = match.get("fav")
    fav_ip = match.get("fav_ip")
    return fav in ("home", "away") and fav_ip is not None and fav_ip >= FAV_IP_MIN


def rule_code(match: Dict) -> Optional[str]:
    """返回命中规则的代号：R1 / R2 / None（R1 优先）。"""
    if is_r1(match):
        return RULE_TOP
    if is_r2(match):
        return RULE_FALLBACK
    return None


def select_for_day(matches: List[Dict]) -> (Optional[str], List[Dict]):
    """按日两级筛选：R1 有货用 R1；否则用 R2；都无货返回 (None, [])。"""
    top = [m for m in matches if is_r1(m)]
    if top:
        return RULE_TOP, top
    fallback = [m for m in matches if is_r2(m)]
    if fallback:
        return RULE_FALLBACK, fallback
    return None, []
