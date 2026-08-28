"""独立大小球盘口（SportMonks O/U）方向 + 置信度门控模型

数据来源
--------
odds_snapshots 表（SportMonks 通用赔率快照）中的 over_odds / under_odds / goal_line 列。
收集器对 SportMonks 覆盖的比赛自动写入（多家博彩公司 × 多条盘口线 × 多次快照），
历史缺口由 backend/tools/_backfill_sm_ou.py 全量回补（180 天，765 场，5.9 万行）。

为什么独立于 TTG
----------------
竞彩 TTG 总进球盘是"0/1/2/3/4/5/6/7+"分段定价，隐含 P大（>2.5 球）受档口间距与
抽水扭曲；SportMonks O/U 是连续盘口（over/under 双向对赌），定价更干净。
180 天全量验证（backend/tools/_scan_ou_conf.py，N=1002，2.5 线）：
    |Δ|≥0.00 → 60.0%  (覆盖 100%)      ← O/U 单市场 baseline（优于 TTG 同覆盖）
    |Δ|≥0.08 → 66.6%  (覆盖 41.8%)
    |Δ|≥0.10 → 70.8%  (覆盖 30.0%)     ← standard 档
    |Δ|≥0.15 → 76.2%  (覆盖 12.6%)     ← strict 档
对照 TTG standard 门控：覆盖 31.1% 时命中 65.6%。O/U 门控同覆盖下高 +5.2pp。

多线扩展（2026-08）
------------------
SM 的 O/U 是全系列连续盘（0.5~6.5，0.25 阶梯），Pinnacle 主盘为整半线
0.5/1.5/2.5/3.5/4.5/5.5（近 180 天每条约 1000 场、Pinnacle 报价 ~90%）。
每条整半线是独立下注市场（0.5 线=总进球≥1、1.5=≥2、2.5=≥3、3.5=≥4、4.5=≥5），
全部纳入 lines 独立判定。主判定线仍为 2.5（与 TTG 口径一致），缺 2.5 时回退最近整半线。

聚合口径（与验证脚本一致）
--------------------------
同一 match 内优先 Pinnacle 报价，无 Pinnacle 时用全部博彩公司中位数；
多快照取最新值（同 bookmaker×line 后写覆盖）。隐含概率做去抽水归一化（1/ov / (1/ov+1/un)）。
"""
from typing import Optional

# 门控档位：{tier: {"delta": |P大-0.5| 判定门槛, "label": 展示名}}
# 判定规则：P大 >= 0.5+delta → 大球；P大 <= 0.5-delta → 小球；否则 skip
TIERS = {
    "full":     {"delta": 0.00, "label": "全量 O/U"},
    "loose":    {"delta": 0.08, "label": "宽松 |Δ|≥.08"},
    "standard": {"delta": 0.10, "label": "标准 |Δ|≥.10"},
    "strict":   {"delta": 0.15, "label": "严格 |Δ|≥.15"},
}
DEFAULT_TIER = "standard"

# 主判定线（与竞彩 TTG 大小球口径一致：总进球 > 2.5 即 ≥3 球）
_GOAL_LINE = 2.5


def _implied_p(over: float, under: float) -> Optional[float]:
    """O/U 双向赔率 → 去抽水归一化隐含大球概率"""
    if over is None or under is None or over <= 1.0 or under <= 1.0:
        return None
    inv_ov = 1.0 / over
    inv_un = 1.0 / under
    if inv_ov + inv_un <= 0:
        return None
    return inv_ov / (inv_ov + inv_un)


def _is_half_line(gl: float) -> bool:
    """整半线：x.0 / x.5（Pinnacle 主盘），0.5 ~ 6.5"""
    return 0.5 <= gl <= 6.5 and abs(gl * 2 - round(gl * 2)) <= 1e-6


def _verdict(p_big: float, tier_key: str) -> str:
    t = TIERS.get(tier_key, TIERS[DEFAULT_TIER])
    thr = t["delta"]
    if p_big >= 0.5 + thr:
        return "over"
    if p_big <= 0.5 - thr:
        return "under"
    return "skip"


def ou_market_from_rows(ou_rows, goal_line: float = _GOAL_LINE) -> Optional[dict]:
    """由一场比赛的全部 O/U 行计算方向 + 置信度（支持多线独立判定）。

    ou_rows: 可迭代，每项形如 (bookmaker, goal_line, over_odds, under_odds) 或
             dict with keys bookmaker/goal_line/over_odds/under_odds。
    goal_line: 主判定线，默认 2.5；缺失时回退到最近有数据的整半线。
    返回 None = 无任何有效线数据（无信号）。返回 dict:
      p_big / p_small   主判定线的归一化隐含大球/小球概率
      direction         "over" | "under" | "skip"（standard 档，主判定线）
      goal_line         主判定线（默认 2.5，回退后可能为其他整半线）
      confidence        max(P大, P小)
      delta             |P大 - 0.5|
      n_books           主判定线参与中位数的博彩公司数（Pinnacle 优先）
      source            "sportmonks_ou"
      tiers             {tier: direction}（主判定线各档位判定）
      lines             {goal_line: {同字段去 lines/tiers 外的 verdict}}（全部整半线独立判定）
    """
    if not ou_rows:
        return None
    # 按 (bookmaker, line) 归集：同 (bm, line) 出现多次（多快照）时取最新，需依赖输入顺序靠后的为最新
    best = {}
    for row in ou_rows:
        if isinstance(row, (tuple, list)):
            bm, gl, ov, un = row[0], row[1], row[2], row[3]
        else:
            bm = row.get("bookmaker")
            gl = row.get("goal_line")
            ov = row.get("over_odds")
            un = row.get("under_odds")
        if bm is None or gl is None:
            continue
        try:
            gl_f = float(gl)
        except (TypeError, ValueError):
            continue
        p = _implied_p(_to_f(ov), _to_f(un))
        if p is None:
            continue
        best[(str(bm), gl_f)] = p  # 后写覆盖前写 = 取最新快照

    # 按线归集
    by_line: dict[float, dict] = {}
    for (bm, gl_f), p in best.items():
        by_line.setdefault(gl_f, {})[bm] = p

    # 主判定线：优先目标线，否则最近整半线
    main = goal_line if goal_line in by_line else None
    if main is None:
        half = [gl for gl in by_line if _is_half_line(gl)]
        if half:
            main = min(half, key=lambda g: abs(g - goal_line))
    if main is None:
        return None

    def _line_p(gl: float):
        vals = by_line[gl]
        pinn = [p for bm, p in vals.items() if bm == "Pinnacle"]
        arr = pinn if pinn else list(vals.values())
        arr.sort()
        pb = max(0.0, min(1.0, arr[len(arr) // 2]))
        return round(pb, 4), len(vals)

    p_main, n_main = _line_p(main)

    lines = {}
    for gl in sorted(by_line):
        if not _is_half_line(gl):
            continue
        pb, n_b = _line_p(gl)
        lines[str(gl)] = {
            "p_big": pb,
            "p_small": round(1.0 - pb, 4),
            "goal_line": gl,
            "direction": _verdict(pb, DEFAULT_TIER),
            "confidence": round(max(pb, 1.0 - pb), 4),
            "delta": round(abs(pb - 0.5), 4),
            "n_books": n_b,
            "tiers": {t: _verdict(pb, t) for t in TIERS},
        }

    return {
        "p_big": p_main,
        "p_small": round(1.0 - p_main, 4),
        "goal_line": main,
        "direction": _verdict(p_main, DEFAULT_TIER),
        "confidence": round(max(p_main, 1.0 - p_main), 4),
        "delta": round(abs(p_main - 0.5), 4),
        "n_books": n_main,
        "source": "sportmonks_ou",
        "tiers": {t: _verdict(p_main, t) for t in TIERS},
        "lines": lines,
    }


def _to_f(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
