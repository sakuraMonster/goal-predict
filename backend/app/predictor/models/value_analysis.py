"""价值投注分析引擎 —— 将 Buchdahl 框架与足彩量化框架的共识固化为确定性逻辑。

共识来源（两套 skill 的交集）：
- 价值 = 真实概率 × 竞彩赔率 − 1 > 0（football-betting-quant-framework 镜片 1/2）
- 「真实概率」的最佳估计 = 锐利盘去水后的概率（Buchdahl「收盘线≈真实概率 / 打不过就加入」）
- 冷门端抽水最重、价值最假（favourite-longshot bias，镜片 6）
- 风险 = 抽水(margin) + 信号强度(噪声带) + 冷热门 + 盘口漂移 + 基准可信度

覆盖范围（与数据源对齐）：
- 有锐利盘基准 → 可做「价值判定 + 风险」：1X2（bet365 3-way）、大小球方向（竞彩 TTG 隐含 P大 vs bet365 O/U）
- 无锐利盘基准 → 仅「风险提示」：让球（欧盘3-way vs 亚盘2-way 口径不对齐）、总进球计数(TTG 选数)、比分(CRS)、半全场(HAFU)

本模块纯逻辑、不依赖数据库，供 API 层调用；所有概率/赔率以 dict 形式传入。
"""
from __future__ import annotations

from typing import Optional

# 价值判定阈值：EV 超过 +2% 算「有价值」，低于 −2% 算「偏贵/无价值」（Buchdahl EV>102% 的简化）。
VALUE_THRESHOLD = 0.02
# 大小球方向价值阈值：竞彩 P大 与锐利盘 P大 的偏离超过 2pp 才认作可操作价值。
OU_EDGE_THRESHOLD = 0.02


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _to_f(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _odds_dict(raw) -> dict:
    """把任意输入规整成 {key: float>0} 的赔率映射。"""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for k, v in raw.items():
        f = _to_f(v)
        if f is not None and f > 1.0:
            out[str(k)] = f
    return out


def devig(odds: dict) -> Optional[dict]:
    """去水（基本归一化）：{outcome: odds} → {outcome: 归一化概率}。无效返回 None。"""
    if not odds:
        return None
    inv = {k: 1.0 / o for k, o in odds.items() if o and o > 1.0}
    if not inv:
        return None
    total = sum(inv.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in inv.items()}


def devig_power(odds: dict, max_iter: int = 200) -> Optional[dict]:
    """幂法去水（power method）：P_i = (1/odds_i)^k / Σ(1/odds_j)^k，k 使概率和=1。

    相比基本归一化，幂法把 margin 更多压向高赔率（冷门）端，修正 favourite-longshot bias，
    是 Buchdahl 去水四法中适用于 3-way 1X2 的方法。数值失败时退化为基本归一化。
    """
    o = _odds_dict(odds)
    if not o:
        return None
    inv = [1.0 / x for x in o.values()]

    def _s(k: float) -> float:
        return sum(x ** k for x in inv)

    lo, hi = 1.0, 2.0
    while _s(hi) > 1.0 and hi < 64.0:
        hi *= 2.0
    if _s(hi) >= 1.0:
        # 极端输入下无法靠幂法收敛，退化为基本归一化
        return devig(odds)

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        if _s(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    raw = {name: (1.0 / x) ** k for name, x in o.items()}
    total = sum(raw.values())
    if total <= 0:
        return None
    return {name: p / total for name, p in raw.items()}


def overround(odds: dict) -> Optional[float]:
    """overround = Σ(1/odds) − 1（庄家抽水比例）。"""
    if not odds:
        return None
    inv = [1.0 / o for o in odds.values() if o and o > 1.0]
    if not inv:
        return None
    return max(0.0, sum(inv) - 1.0)


def _ttg_p_big(ttg_odds: dict, goal_line: float = 2.5) -> Optional[float]:
    """竞彩 TTG 总进球赔率 → 去水后 P大（总进球 > goal_line 的概率）。"""
    d = devig(ttg_odds)
    if not d:
        return None
    p_big = sum(p for k, p in d.items() if _to_f(k) is not None and _to_f(k) > goal_line)
    return max(0.0, min(1.0, p_big))


# ---------------------------------------------------------------------------
# 价值判定
# ---------------------------------------------------------------------------
def market_value(jczq_odds: dict, sharp_odds: dict, threshold: float = VALUE_THRESHOLD) -> dict:
    """通用多向市场价值判定：以锐利盘去水概率为「真实概率」，逐项算 EV。

    jczq_odds / sharp_odds: 同 key 的 {outcome: odds}（如 1X2 的 home/draw/away）。
    返回：
      verdict    "有价值" | "中性" | "无价值" | "无基准"
      best_side  最大 EV 的 outcome
      best_ev    最大 EV
      outcomes   {outcome: {odds, fair_prob, ev}}
      fair_prob  锐利盘去水概率（真实概率）
    """
    jczq = _odds_dict(jczq_odds)
    sharp = _odds_dict(sharp_odds)
    if not jczq or not sharp:
        return {"verdict": "无基准", "reason": "缺竞彩或锐利盘赔率"}

    # 幂法去水（把抽水更多压向冷门端），修正 favourite-longshot bias；
    # 相比基本归一化，best_side 不再系统性偏向冷门长尾（回测命中 29.7%→36.5%）。
    fair = devig_power(sharp)
    if not fair:
        return {"verdict": "无基准", "reason": "锐利盘无法去水"}

    outcomes: dict = {}
    for k, o in jczq.items():
        if k not in fair:
            continue
        p = fair[k]
        outcomes[k] = {
            "odds": o,
            "fair_prob": round(p, 4),
            "ev": round(p * o - 1.0, 4),
        }
    if not outcomes:
        return {"verdict": "无基准", "reason": "竞彩与锐利盘无重叠 outcome"}

    best_side = max(outcomes, key=lambda k: outcomes[k]["ev"])
    best_ev = outcomes[best_side]["ev"]
    if best_ev >= threshold:
        verdict = "有价值"
    elif best_ev <= -threshold:
        verdict = "无价值"
    else:
        verdict = "中性"

    return {
        "verdict": verdict,
        "best_side": best_side,
        "best_ev": best_ev,
        "outcomes": outcomes,
        "fair_prob": {k: round(p, 4) for k, p in fair.items()},
    }


def ou_value(jczq_p_big: Optional[float], sharp_p_big: Optional[float],
             threshold: float = OU_EDGE_THRESHOLD) -> dict:
    """大小球方向错价：比较竞彩 TTG 隐含 P大 与锐利盘 O/U 隐含 P大。

    delta = sharp_p_big − jczq_p_big。
      delta < 0 → 竞彩大球偏贵（小球侧相对便宜）
      delta > 0 → 竞彩大球偏便宜（大球侧相对便宜）

    注意：竞彩 TTG 抽水高达 ~25%，此错价通常不足以覆盖抽水，仅作方向参考，
    不构成可下注的「有价值」（价值判定仅 1X2 成立）。返回 verdict / cheap_side / edge。
    """
    if jczq_p_big is None or sharp_p_big is None:
        return {"verdict": "无基准", "reason": "缺竞彩 TTG 或锐利盘 O/U"}
    delta = sharp_p_big - jczq_p_big
    if delta <= -threshold:
        verdict, cheap = "大球偏贵", "小球"
    elif delta >= threshold:
        verdict, cheap = "小球偏贵", "大球"
    else:
        verdict, cheap = "中性", None
    return {
        "verdict": verdict,
        "cheap_side": cheap,
        "edge": round(delta, 4),
        "jczq_p_big": round(jczq_p_big, 4),
        "sharp_p_big": round(sharp_p_big, 4),
    }


# ---------------------------------------------------------------------------
# 风险评分
# ---------------------------------------------------------------------------
def _level_for(value: Optional[float], low: float, high: float) -> str:
    """连续值 → 风险等级：≤low 低 / (low, high] 中 / >high 高；None → 高（未知即风险）。"""
    if value is None:
        return "高"
    if value <= low:
        return "低"
    if value <= high:
        return "中"
    return "高"


_RISK_ORDER = {"低": 0, "中": 1, "高": 2}


def _merge_level(levels) -> str:
    """取最高风险等级。"""
    return max(levels, key=lambda l: _RISK_ORDER.get(l, 0)) if levels else "低"


def margin_flag(odds: dict) -> dict:
    """抽水风险：竞彩该玩法的 overround。"""
    ornd = overround(_odds_dict(odds))
    if ornd is None:
        return {"key": "margin", "level": "高", "label": "抽水未知", "desc": "无有效赔率，无法评估抽水。"}
    lvl = _level_for(ornd, 0.08, 0.15)
    return {
        "key": "margin",
        "level": lvl,
        "label": f"竞彩抽水 {ornd * 100:.1f}%",
        "desc": "抽水越高，需要的 edge 越大，长期越难覆盖。" + ("竞彩总进球/比分/半全场类抽水通常 25%+，极其重。" if ornd > 0.2 else ""),
    }


def signal_flag(p_big: Optional[float]) -> dict:
    """信号强度风险：|P大 − 0.5| 越小越靠近噪声带（历史命中≈50%），风险越高。"""
    if p_big is None:
        return {"key": "signal", "level": "高", "label": "无方向信号", "desc": "无有效大小球信号。"}
    delta = abs(p_big - 0.5)
    if delta < 0.08:
        lvl = "高"
        label = f"噪声带 P大 {p_big * 100:.0f}%"
        desc = "方向落在噪声带，历史命中≈50%，等同抛硬币。"
    elif delta < 0.15:
        lvl = "中"
        label = f"弱信号 P大 {p_big * 100:.0f}%"
        desc = "方向有倾向但不够明确。"
    else:
        lvl = "低"
        label = f"明确信号 P大 {p_big * 100:.0f}%"
        desc = "方向明确，信号较强。"
    return {"key": "signal", "level": lvl, "label": label, "desc": desc}


def longshot_flag(best_odds: Optional[float]) -> dict:
    """冷热门风险：目标选项赔率越高，越靠近冷门/长尾端，抽水越重、价值越假。"""
    lvl = _level_for(best_odds, 2.0, 4.0)
    if best_odds is None:
        return {"key": "longshot", "level": "高", "label": "赔率未知", "desc": "无有效赔率。"}
    return {
        "key": "longshot",
        "level": lvl,
        "label": f"目标赔率 {best_odds:.2f}",
        "desc": "冷门端（高赔率）是市场抽水最重、价值最假的区域。" if lvl in ("中", "高") else "热门端赔率，抽水相对轻。",
    }


def movement_flag(jczq_odds: dict, early_odds: dict) -> dict:
    """盘口漂移风险：开赛前 vs 最新赔率的变化幅度（信息是否已被消化）。"""
    cur = _odds_dict(jczq_odds)
    early = _odds_dict(early_odds)
    if not cur or not early:
        return {"key": "movement", "level": "中", "label": "无历史盘口", "desc": "缺少开赛前早盘，无法评估漂移。"}
    common = [k for k in cur if k in early]
    if not common:
        return {"key": "movement", "level": "中", "label": "盘口结构变化", "desc": "早盘与现盘结构不一致。"}
    # 以隐含概率变化的最大绝对值度量漂移
    cur_d = devig(cur) or {}
    early_d = devig(early) or {}
    max_move = max((abs(cur_d.get(k, 0) - early_d.get(k, 0)) for k in common), default=0.0)
    lvl = _level_for(max_move, 0.03, 0.08)
    return {
        "key": "movement",
        "level": lvl,
        "label": f"盘口漂移 {max_move * 100:.1f}pp",
        "desc": "漂移大说明信息已被市场消化、或临场变数多。" if lvl in ("中", "高") else "盘口稳定，无明显漂移。",
    }


def cold_risk_flag(jczq_odds: dict, sharp_odds: dict) -> Optional[dict]:
    """冷门风险系数：押冷门（赌热门不赢）的风险分层。

    主信号 = 竞彩热门赔率（热门越强，冷门越难赢）；
    微调 = edge_pp（锐利盘公平概率 − 竞彩隐含概率）：锐利盘越看好冷门，风险越低。
    仅对 1X2（3 个 outcome）有意义；冷门侧无锐利盘基准时退化为仅看热门赔率。
    """
    jczq = _odds_dict(jczq_odds)
    if len(jczq) < 3:
        return None
    fav = min(jczq, key=lambda k: jczq[k])
    fav_odds = jczq[fav]
    fav_zh = {"home": "主胜", "draw": "平", "away": "客胜"}.get(fav, fav)

    # 主档：竞彩热门赔率
    if fav_odds < 1.3:
        base, hot_label = 2, "超强热门"
    elif fav_odds < 2.0:
        base, hot_label = 1, "中等热门"
    else:
        base, hot_label = 0, "无强热门"

    # 微调：冷门两侧中锐利盘相对竞彩最看好的 edge_pp
    sharp = _odds_dict(sharp_odds)
    fair = devig(sharp) if sharp else None
    best_edge = None
    if fair:
        for k in jczq:
            if k == fav or k not in fair:
                continue
            e = fair[k] - (1.0 / jczq[k])
            if best_edge is None or e > best_edge:
                best_edge = e

    adj = 0
    if best_edge is not None:
        if best_edge >= 0.05:
            adj = -1
        elif best_edge < 0.02:
            adj = +1

    levels = ["低", "中", "高"]
    level = levels[max(0, min(2, base + adj))]

    label = f"冷门风险·{level}（热门{fav_zh} {fav_odds:.2f}）"
    desc = f"押冷门（赌热门{fav_zh}不赢）的风险：竞彩热门赔率 {fav_odds:.2f}（{hot_label}）。"
    if best_edge is not None:
        label += f" edge {best_edge * 100:.1f}%"
        if best_edge < 0.02:
            desc += " 锐利盘亦看衰冷门，风险上修。"
        elif best_edge >= 0.05:
            desc += " 锐利盘相对看好冷门，风险下修。"
    return {"key": "cold_risk", "level": level, "label": label, "desc": desc}


def benchmark_flag(has_benchmark: bool, n_books: Optional[int] = None) -> dict:
    """基准可信度风险：锐利盘是否可估。"""
    if not has_benchmark:
        return {"key": "benchmark", "level": "高", "label": "无锐利盘基准", "desc": "该玩法无锐利盘，无法做价值判定，仅能提示风险。"}
    if n_books is None or n_books >= 2:
        lvl = "低"
        desc = "锐利盘基准可用。"
    elif n_books == 1:
        lvl = "中"
        desc = "锐利盘仅 1 家，基准可信度有限。"
    else:
        lvl = "高"
        desc = "锐利盘基准缺失。"
    label = f"锐利盘基准（{n_books}家）" if n_books else "锐利盘基准"
    return {"key": "benchmark", "level": lvl, "label": label, "desc": desc}


def assemble_risk(flags: list[dict]) -> dict:
    """汇总风险：各维度 flag + 总等级。"""
    level = _merge_level([f["level"] for f in flags])
    return {"level": level, "flags": flags}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def analyze_match(
    jczq: Optional[dict] = None,
    sharp_1x2: Optional[dict] = None,
    sharp_ou_p_big: Optional[float] = None,
    sharp_ou_n_books: Optional[int] = None,
    jczq_early: Optional[dict] = None,
) -> dict:
    """分析单场比赛的价值与风险。

    参数（均为 dict / 标量）：
      jczq         竞彩最新快照：{had:{home,draw,away}, hhad:{line,home,draw,away},
                     ttg:{0:..,1:..,...}, crs:{...}, hafu:{...}}
      sharp_1x2    锐利盘 1X2：{home,draw,away}
      sharp_ou_p_big   锐利盘大小球去水后 P大（2.5 线）
      sharp_ou_n_books 锐利盘大小球参与庄家数（用于基准可信度）
      jczq_early   竞彩最早快照（用于盘口漂移，可省）
    返回 {markets: {...}, risk_summary: {...}}
    """
    jczq = jczq or {}
    had = _odds_dict(jczq.get("had"))
    hhad = _odds_dict(jczq.get("hhad"))
    ttg = _odds_dict(jczq.get("ttg"))
    crs = _odds_dict(jczq.get("crs"))
    hafu = _odds_dict(jczq.get("hafu"))

    markets: dict = {}

    # --- 1X2 胜平负：价值 + 风险 ---
    if had:
        mv = market_value(had, sharp_1x2 or {})
        best_odds = (mv.get("outcomes") or {}).get(mv.get("best_side"), {}).get("odds")
        flags = [
            margin_flag(had),
            benchmark_flag(bool(_odds_dict(sharp_1x2 or {}))),
            longshot_flag(best_odds),
            movement_flag(had, (jczq_early or {}).get("had")),
        ]
        _cr = cold_risk_flag(had, sharp_1x2 or {})
        if _cr:
            flags.append(_cr)
        markets["1x2"] = {
            "name": "胜平负",
            "jczq_odds": had,
            "sharp_odds": _odds_dict(sharp_1x2 or {}),
            "value": mv,
            "risk": assemble_risk(flags),
        }

    # --- 让球：仅风险（竞彩让球=欧盘3-way -1，锐利盘=亚盘2-way，口径不对齐，无法直接估值）---
    if hhad:
        line_label = jczq.get("hhad", {}).get("line") if isinstance(jczq.get("hhad"), dict) else None
        flags = [
            margin_flag(hhad),
            longshot_flag(_top_odds(hhad)),
        ]
        markets["handicap"] = {
            "name": f"让球({line_label})" if line_label is not None else "让球",
            "jczq_odds": hhad,
            "value": {"verdict": "仅风险", "reason": "竞彩让球为欧盘3-way，锐利盘为亚盘2-way，口径不对齐，无法估值"},
            "risk": assemble_risk(flags),
        }

    # --- 大小球方向：价值 + 风险 ---
    if ttg:
        jczq_p_big = _ttg_p_big(ttg)
        ov_val = ou_value(jczq_p_big, sharp_ou_p_big)
        flags = [
            margin_flag(ttg),
            signal_flag(jczq_p_big),
            benchmark_flag(sharp_ou_p_big is not None, sharp_ou_n_books),
        ]
        markets["ou"] = {
            "name": "大小球",
            "jczq_p_big": round(jczq_p_big, 4) if jczq_p_big is not None else None,
            "sharp_p_big": round(sharp_ou_p_big, 4) if sharp_ou_p_big is not None else None,
            "value": ov_val,
            "risk": assemble_risk(flags),
        }

    # --- 总进球计数（TTG 选数）：仅风险 ---
    if ttg:
        flags = [margin_flag(ttg), signal_flag(_ttg_p_big(ttg))]
        markets["ttg"] = {
            "name": "总进球(选数)",
            "jczq_odds": ttg,
            "value": {"verdict": "仅风险", "reason": "无锐利盘计数基准，仅提示风险"},
            "risk": assemble_risk(flags),
        }

    # --- 比分：仅风险 ---
    if crs:
        markets["crs"] = {
            "name": "比分",
            "jczq_odds": crs,
            "value": {"verdict": "仅风险", "reason": "无锐利盘比分基准，仅提示风险"},
            "risk": assemble_risk([margin_flag(crs), longshot_flag(_top_odds(crs))]),
        }

    # --- 半全场：仅风险 ---
    if hafu:
        markets["hafu"] = {
            "name": "半全场",
            "jczq_odds": hafu,
            "value": {"verdict": "仅风险", "reason": "无锐利盘半全场基准，仅提示风险"},
            "risk": assemble_risk([margin_flag(hafu), longshot_flag(_top_odds(hafu))]),
        }

    # --- 汇总 ---
    risk_summary = _summarize(markets)
    return {"markets": markets, "risk_summary": risk_summary}


def _top_odds(odds: dict) -> Optional[float]:
    d = _odds_dict(odds)
    if not d:
        return None
    return min(d.values())  # 最低赔率 = 最可能 = 参考「目标」；冷热门用最低赔率档


def _summarize(markets: dict) -> dict:
    """汇总全场：最有价值的玩法 + 整体风险等级 + 关键提示。"""
    # 找最有价值的「价值判定」玩法（排除仅风险）
    best_market = None
    best_ev = None
    value_hits = []
    for mk, m in markets.items():
        v = m.get("value") or {}
        if v.get("verdict") == "有价值":
            value_hits.append(mk)
        ev = v.get("best_ev")
        if ev is not None and (best_ev is None or ev > best_ev):
            best_ev = ev
            best_market = mk

    # 整体风险：以胜平负(1X2)为主，避免被总进球/比分/半全场固定 25%+ 抽水顶成恒高
    core = markets.get("1x2")
    if core and core.get("risk"):
        overall_risk = core["risk"]["level"]
    else:
        levels = [m["risk"]["level"] for m in markets.values() if m.get("risk")]
        overall_risk = _merge_level(levels)

    hints = []
    if value_hits:
        hints.append(f"有价值玩法：{', '.join(value_hits)}")
    if best_market and best_ev is not None and best_ev > 0:
        hints.append(f"最优 EV：{markets[best_market]['name']} {best_ev:+.1%}")
    if overall_risk == "高":
        hints.append("整体风险高：抽水重或信号在噪声带，不建议重仓。")
    if not value_hits:
        hints.append("本场未发现明显价值（多数场次属正常——市场近有效）。")

    return {
        "overall_risk": overall_risk,
        "value_markets": value_hits,
        "best_market": best_market,
        "best_ev": best_ev,
        "hints": hints,
    }
