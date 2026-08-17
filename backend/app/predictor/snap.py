"""SNAP Top2 算法 —— 距离预期进球最近的2个整数，唯一权威实现

所有模块（预测/判定/前端）都应通过此模块调用，杜绝重复实现。
"""

SNAP_DOWN = 0.05  # 0.04(腓特烈斯塔) < 0.05 < 0.09(赫根) → 精确隔离
SNAP_UP = 0.93    # 0.91(哈尔姆斯塔德) < 0.93 < 0.95(奥斯陆) → 精确隔离
MAX_GOALS = 6  # 6=6+


def snap_effective(expected_goals: float) -> float:
    """SNAP 修正后的 effective λ：
    - 小数部分 < SNAP_DOWN(0.05) → 降级: 中心落 int(λ)-0.5 (cap 0)
    - 小数部分 > SNAP_UP(0.93)  → 升级: 中心落 int(λ)+1.5 (cap 6)
    - 否则保持原值

    V2 修正：降级/升级只把 effective 中心偏移 0.5 档，
    使 SNAP Top2 落在 [floor-1, floor] / [ceil, ceil+1]，
    保留 λ 整数附近的概率众数。
    旧实现将 effective 整体偏移 ±1（3.05→2→[2,1]），
    会丢掉概率最高的整数本身（3.05 丢 3），命中率更低。
    """
    frac = expected_goals - int(expected_goals)
    if frac < SNAP_DOWN:
        return float(max(0, int(expected_goals) - 0.5))
    elif frac > SNAP_UP:
        return float(min(MAX_GOALS, int(expected_goals) + 1.5))
    return expected_goals


def snap_top2(expected_goals: float) -> list[int]:
    """返回距离 effective λ 最近的2个整数（0-6 范围内）"""
    effective = snap_effective(expected_goals)
    dists = [(i, abs(effective - i)) for i in range(MAX_GOALS + 1)]
    dists.sort(key=lambda x: x[1])
    return [dists[0][0], dists[1][0]]


def snap_top3(expected_goals: float) -> list[int]:
    """返回距离 effective λ 最近的3个整数（0-6 范围内）"""
    effective = snap_effective(expected_goals)
    dists = [(i, abs(effective - i)) for i in range(MAX_GOALS + 1)]
    dists.sort(key=lambda x: x[1])
    return [dists[0][0], dists[1][0], dists[2][0]]


def snap_top2_norway(expected_goals: float) -> list[int]:
    """挪超专属 SNAP: 联赛系统性爆大球(actual/GL=1.53, 5+球率36%)
    effective λ + 0.2 微调 + Top3 扩覆盖，兼顾大球识别与正常比赛不丢失"""
    effective = snap_effective(expected_goals) + 0.2
    effective = min(MAX_GOALS, effective)
    dists = [(i, abs(effective - i)) for i in range(MAX_GOALS + 1)]
    dists.sort(key=lambda x: x[1])
    return [dists[0][0], dists[1][0], dists[2][0]]


def judge_goals(total_goals: int, expected_goals: float) -> int:
    """判定进球数是否命中：实际总进球是否在 SNAP Top2 范围内"""
    top2 = snap_top2(expected_goals)
    return 1 if total_goals in top2 else -1
