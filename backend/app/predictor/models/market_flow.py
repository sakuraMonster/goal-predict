from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _parse_score(score: str) -> tuple[int, int] | None:
    if not isinstance(score, str):
        return None
    if "-" not in score:
        return None
    a, b = score.split("-", 1)
    if not a.isdigit() or not b.isdigit():
        return None
    return int(a), int(b)


def _score_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals == away_goals:
        return "draw"
    return "away"


def _hhad_outcome(home_goals: int, away_goals: int, line: float | None) -> str:
    adj_home = float(home_goals) + float(line or 0.0)
    if adj_home > float(away_goals):
        return "home"
    if adj_home == float(away_goals):
        return "draw"
    return "away"


def _detect_gap(sorted_odds: list[float], gap_abs: float, gap_ratio: float) -> int | None:
    for i in range(len(sorted_odds) - 1):
        cur = sorted_odds[i]
        nxt = sorted_odds[i + 1]
        if cur <= 0:
            continue
        if (nxt - cur) >= gap_abs and (nxt / cur) >= gap_ratio:
            return i
    return None


def _is_defensive(style_tag: str | None) -> bool:
    return bool(style_tag) and ("防守" in str(style_tag))


def _is_open_game(style_tag: str | None) -> bool:
    return bool(style_tag) and ("大开大合" in str(style_tag))


def _matchup_style(home_style_tag: str | None, away_style_tag: str | None) -> str:
    hs_def = _is_defensive(home_style_tag)
    as_def = _is_defensive(away_style_tag)
    hs_open = _is_open_game(home_style_tag)
    as_open = _is_open_game(away_style_tag)

    if (hs_def and not as_open) or (as_def and not hs_open):
        return "防守型"
    if (hs_open and not as_def) or (as_open and not hs_def):
        return "大开大合"
    return "均衡"


def _style_priority(matchup_style: str) -> list[int]:
    if matchup_style == "防守型":
        return [1, 2, 3, 4, 5, 0]
    if matchup_style == "大开大合":
        return [3, 4, 2, 5, 1, 0]
    return [2, 3, 1, 4, 0, 5]


def _ttg_get(ttg: dict[Any, float] | None, g: int) -> float | None:
    if not ttg:
        return None
    v = ttg.get(str(g))
    if isinstance(v, (int, float)) and float(v) > 0:
        return float(v)
    v2 = ttg.get(g)
    if isinstance(v2, (int, float)) and float(v2) > 0:
        return float(v2)
    return None


def _had_preference(had: dict[str, float] | None) -> str | None:
    if not had:
        return None
    h = had.get("home")
    d = had.get("draw")
    a = had.get("away")
    if not all(isinstance(x, (int, float)) and float(x) > 0 for x in [h, d, a]):
        return None
    items = {"home": float(h), "draw": float(d), "away": float(a)}
    return min(items.items(), key=lambda x: x[1])[0]


def _hhad_preference(hhad: dict[str, float] | None) -> str | None:
    if not hhad:
        return None
    h = hhad.get("home")
    d = hhad.get("draw")
    a = hhad.get("away")
    candidates: dict[str, float] = {}
    if isinstance(h, (int, float)) and float(h) > 0:
        candidates["home"] = float(h)
    if isinstance(d, (int, float)) and float(d) > 0:
        candidates["draw"] = float(d)
    if isinstance(a, (int, float)) and float(a) > 0:
        candidates["away"] = float(a)
    if not candidates:
        return None
    return min(candidates.items(), key=lambda x: x[1])[0]


def _crs_odd(crs: dict[str, float] | None, score: str | None) -> float | None:
    if not crs or not score:
        return None
    v = crs.get(score)
    if isinstance(v, (int, float)) and float(v) > 0:
        return float(v)
    return None


def _crs_groups_v2(crs: dict[str, float] | None) -> tuple[dict, dict, dict, dict, dict, dict]:
    by_home: dict[int, list[tuple[str, int, float]]] = {}
    by_away: dict[int, list[tuple[str, int, float]]] = {}
    by_total: dict[int, list[tuple[str, int, int, float]]] = {}
    if not isinstance(crs, dict):
        return by_home, by_away, by_total, {}, {}, {}
    for sk, odd in crs.items():
        if isinstance(sk, str) and "-" in sk:
            try:
                h, a = sk.split("-", 1)
                h_i = int(h); a_i = int(a)
            except Exception:
                continue
        else:
            continue
        if not isinstance(odd, (int, float)) or float(odd) <= 0:
            continue
        v = float(odd)
        by_home.setdefault(h_i, []).append((sk, a_i, v))
        by_away.setdefault(a_i, []).append((sk, h_i, v))
        by_total.setdefault(h_i + a_i, []).append((sk, h_i, a_i, v))
    home_best = {h: min([v for _, _, v in xs]) for h, xs in by_home.items()}
    away_best = {a: min([v for _, _, v in xs]) for a, xs in by_away.items()}
    total_best = {g: min([v for _, _, _, v in xs]) for g, xs in by_total.items()}
    return by_home, by_away, by_total, home_best, away_best, total_best


def _crs_pairs_signal(crs: dict[str, float] | None) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    def cmp(s1, s2, label):
        o1 = _crs_odd(crs, s1)
        o2 = _crs_odd(crs, s2)
        if o1 is None or o2 is None:
            return
        r = float(o1) / float(o2)
        if r < 0.92:
            favor = s1
        elif r > 1.08:
            favor = s2
        else:
            favor = "tie"
        pairs.append({"label": label, "s1": s1, "s2": s2, "o1": float(o1), "o2": float(o2), "ratio": float(r), "favor": favor})
    cmp("2-0", "1-0", "主队进2 vs 1 (零封)")
    cmp("2-1", "1-1", "主队进2 vs 1 (客进1)")
    cmp("3-1", "2-1", "主队进3 vs 2 (客进1)")
    cmp("2-1", "1-0", "2-1 vs 1-0 (主胜轴)")
    cmp("0-2", "0-1", "客队进2 vs 1 (零封)")
    cmp("1-2", "1-1", "客队进2 vs 1 (主进1)")
    cmp("1-3", "1-2", "客队进3 vs 2 (主进1)")
    cmp("1-2", "0-1", "1-2 vs 0-1 (客胜轴)")
    cmp("1-1", "0-0", "平局 1-1 vs 0-0")
    cmp("1-1", "2-2", "平局 1-1 vs 2-2")
    cmp("0-0", "1-1", "平局 0-0 vs 1-1")
    return pairs


def _home_away_goals_signal(home_best: dict[int, float], away_best: dict[int, float]) -> dict[str, Any]:
    out: dict[str, Any] = {"home_pairs": [], "away_pairs": []}
    for h1, h2 in [(1, 2), (0, 1), (1, 3), (0, 2)]:
        if h1 in home_best and h2 in home_best:
            r = float(home_best[h1]) / float(home_best[h2])
            if r < 0.9:
                favor = f"h{h1}"
            elif r > 1.1:
                favor = f"h{h2}"
            else:
                favor = "tie"
            out["home_pairs"].append({"pair": (h1, h2), "ratio": float(r), "favor": favor, "best_h1": float(home_best[h1]), "best_h2": float(home_best[h2])})
    for a1, a2 in [(1, 2), (0, 1), (1, 3), (0, 2)]:
        if a1 in away_best and a2 in away_best:
            r = float(away_best[a1]) / float(away_best[a2])
            if r < 0.9:
                favor = f"a{a1}"
            elif r > 1.1:
                favor = f"a{a2}"
            else:
                favor = "tie"
            out["away_pairs"].append({"pair": (a1, a2), "ratio": float(r), "favor": favor, "best_a1": float(away_best[a1]), "best_a2": float(away_best[a2])})
    return out


def _total_ballast_signal_v2(by_total: dict[int, list], ttg: Any, total_best: dict[int, float]) -> dict[str, Any]:
    out: dict[str, Any] = {"bucket_pairs": [], "ttg_vs_crs": []}
    for g1, g2 in [(2, 3), (2, 1), (3, 4), (1, 3)]:
        if g1 in total_best and g2 in total_best:
            r = float(total_best[g1]) / float(total_best[g2])
            if r < 0.9:
                favor = f"g{g1}"
            elif r > 1.1:
                favor = f"g{g2}"
            else:
                favor = "tie"
            out["bucket_pairs"].append({"pair": (g1, g2), "ratio": float(r), "favor": favor})
    for g in sorted(total_best.keys()):
        t = _ttg_get(ttg, int(g))
        if t is None:
            continue
        r = float(total_best[g]) / float(t)
        if r < 0.85:
            tag = "strong"
        elif r > 1.25:
            tag = "weak"
        else:
            tag = "normal"
        out["ttg_vs_crs"].append({"g": int(g), "ratio": float(r), "tag": tag, "ttg": float(t), "best_crs_in_g": float(total_best[g])})
    return out


def _had_hhad_signal(had: dict[str, float], hhad: dict[str, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    hv = {k: float(v) for k, v in (had or {}).items() if k in ("home", "draw", "away") and isinstance(v, (int, float)) and float(v) > 0}
    if not isinstance(hhad, dict):
        return out
    line_raw = hhad.get("line")
    line = float(line_raw) if isinstance(line_raw, (int, float)) else None
    hh = {k: float(v) for k, v in (hhad or {}).items() if k in ("home", "draw", "away") and isinstance(v, (int, float)) and float(v) > 0}
    if len(hv) < 3 or len(hh) < 3 or line is None:
        return out
    out["line"] = line
    if line > 0.2:
        r1 = float(hh["away"]) / float(hv["home"])
        out["hhad_away_over_had_home"] = float(r1)
        if r1 < 0.95:
            out["home_cover_less_likely"] = True
        else:
            out["home_cover_more_likely"] = True
    if line < -0.2:
        r1 = float(hh["home"]) / float(hv["away"])
        out["hhad_home_over_had_away"] = float(r1)
        if r1 < 0.95:
            out["away_cover_less_likely"] = True
        else:
            out["away_cover_more_likely"] = True
    r_draw = float(hv["draw"]) / float(hh["draw"])
    out["had_draw_over_hhad_draw"] = float(r_draw)
    if 0.9 <= r_draw <= 1.1:
        out["draw_anchor_same_as_handicap"] = True
    return out


def _draw_implied_signal_v2(
    had: dict[str, float],
    crs: dict[str, float] | None,
    ttg: Any = None,
    handicap: dict[str, Any] | None = None,
    pairs: list[dict[str, Any]] | None = None,
    total_ballast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    had_draw = had.get("draw") if isinstance(had, dict) else None
    if not isinstance(had_draw, (int, float)) or float(had_draw) <= 0:
        return out
    best_draw_odd: float | None = None
    best_draw_score: str | None = None
    for s in ("0-0", "1-1", "2-2", "3-3"):
        o = _crs_odd(crs, s)
        if o is None:
            continue
        if best_draw_odd is None or float(o) < float(best_draw_odd):
            best_draw_odd = float(o)
            best_draw_score = s
    if best_draw_odd is None or best_draw_score is None:
        return out
    ratio = float(had_draw) / float(best_draw_odd)
    out["had_draw"] = float(had_draw)
    out["best_draw_score"] = best_draw_score
    out["best_draw_odd"] = float(best_draw_odd)
    out["ratio"] = float(ratio)

    def _ttg_get(g: int) -> float | None:
        if ttg is None:
            return None
        if isinstance(ttg, dict):
            v = ttg.get(g) or ttg.get(str(g))
            return float(v) if isinstance(v, (int, float)) and float(v) > 0 else None
        try:
            v = getattr(ttg, "get", None)
            if callable(v):
                rv = v(g) if not isinstance(ttg, list) else None
            else:
                rv = None
            return float(rv) if isinstance(rv, (int, float)) and float(rv) > 0 else None
        except Exception:
            return None

    ttg_g2: float | None = _ttg_get(2)
    crs_11 = _crs_odd(crs, "1-1")
    cond_a: bool = False
    if isinstance(crs_11, (int, float)) and isinstance(ttg_g2, (int, float)) and float(crs_11) > 0 and float(ttg_g2) > 0:
        if float(crs_11) > float(ttg_g2) * 1.1:
            cond_a = True
    if total_ballast and isinstance(total_ballast, dict):
        for tv in total_ballast.get("ttg_vs_crs", []):
            if int(tv.get("g") or -1) == 2 and tv.get("tag") == "weak":
                cond_a = True
                break
    cond_b: bool = False
    if handicap and isinstance(handicap, dict):
        cond_b = not bool(handicap.get("draw_anchor_same_as_handicap"))
    cond_c: bool = True
    if pairs and isinstance(pairs, list):
        draw_tie_count = 0
        for p in pairs:
            favor = p.get("favor")
            if favor in ("tie", "1-1", "0-0", "2-2", "3-3"):
                draw_tie_count += 1
        cond_c = draw_tie_count <= 2
    out["cond_a"] = bool(cond_a)
    out["cond_b"] = bool(cond_b)
    out["cond_c"] = bool(cond_c)

    if ratio < 0.40:
        if cond_a and cond_b:
            out["tag"] = "fake_draw_low"
            out["fake_reason"] = "extreme_weak"
        elif cond_a and cond_b and cond_c:
            out["tag"] = "fake_draw_low"
            out["fake_reason"] = "extreme_combined"
        else:
            out["tag"] = "core_draw_high"
            out["fake_reason"] = None
    elif ratio < 0.90:
        if cond_a and cond_b and cond_c:
            out["tag"] = "fake_draw_low"
            out["fake_reason"] = "combined_weak"
        else:
            out["tag"] = "core_draw_high"
            out["fake_reason"] = None
    elif ratio > 1.15:
        out["tag"] = "true_draw_high"
        out["fake_reason"] = None
    else:
        out["tag"] = "normal"
        out["fake_reason"] = None
    return out


def _hhad_signal_v2(hhad: dict[str, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    def _g(k):
        v = hhad.get(k) if isinstance(hhad, dict) else None
        if isinstance(v, (int,float)) and float(v) > 0: return float(v)
        return None
    hh_h = _g("home")
    hh_d = _g("draw")
    hh_a = _g("away")
    cand: dict[str, float] = {}
    if hh_h is not None: cand["home"] = float(hh_h)
    if hh_d is not None: cand["draw"] = float(hh_d)
    if hh_a is not None: cand["away"] = float(hh_a)
    pref = min(cand.items(), key=lambda x: x[1])[0] if cand else None
    pref_odd = cand.get(pref) if pref in cand else None
    out["pref"] = pref
    out["pref_odd"] = pref_odd
    if pref is not None and pref_odd is not None:
        if pref_odd < 1.55:
            out["pref_bucket"] = "ultra_low"
        elif pref_odd < 1.74:
            out["pref_bucket"] = "very_low"
        elif pref_odd < 2.00:
            out["pref_bucket"] = "sweet_low"
        elif pref_odd < 2.35:
            out["pref_bucket"] = "sweet_mid"
        elif pref_odd < 2.80:
            out["pref_bucket"] = "sweet_high"
        else:
            out["pref_bucket"] = "weak_high"
    else:
        out["pref_bucket"] = None
    if len(cand) >= 3:
        vals = list(cand.values())
        sp = max(vals) - min(vals)
        out["spread"] = float(sp)
        if sp < 1.40:
            out["spread_bucket"] = "close"
        elif sp < 2.00:
            out["spread_bucket"] = "mid_close"
        elif sp < 2.80:
            out["spread_bucket"] = "mid_wide"
        else:
            out["spread_bucket"] = "wide"
    else:
        out["spread"] = None
        out["spread_bucket"] = None
    return out


def _compute_anchor_enforce_scores(
    had: dict[str, float],
    hhad: dict[str, float],
    crs: dict[str, float] | None,
    ttg: Any,
    cfg: "MarketFlowV2Config",
) -> tuple[dict[str, float], dict[str, Any]]:
    by_home, by_away, by_total, home_best, away_best, total_best = _crs_groups_v2(crs)
    home_away = _home_away_goals_signal(home_best, away_best)
    total_ballast = _total_ballast_signal_v2(by_total, ttg, total_best)
    pairs = _crs_pairs_signal(crs)
    handicap = _had_hhad_signal(had, hhad)
    draw_impl = _draw_implied_signal_v2(had, crs, ttg=ttg, handicap=handicap, pairs=pairs, total_ballast=total_ballast)
    hhad_sig = _hhad_signal_v2(hhad)

    weights = {k: float(v) for k, v in (cfg.outcome_enforce_anchor_weights or {}).items()}
    w_hg = float(weights.get("home_goals", 1.0))
    w_ag = float(weights.get("away_goals", 1.0))
    w_tb = float(weights.get("ttg_ballast", 1.0))
    w_cp = float(weights.get("crs_pairs", 1.0))
    w_hh = float(weights.get("had_hhad", 1.5))
    w_di = float(weights.get("draw_implied", 1.5))
    w_protect = float(weights.get("had_pref_protect", 2.0))
    w_hp = float(weights.get("hhad_pref_bucket", 1.5))
    w_hs = float(weights.get("hhad_spread", 1.5))
    w_cons = float(weights.get("hhad_consensus", 1.5))

    black = {"home": 0.0, "draw": 0.0, "away": 0.0}
    white = {"home": 0.0, "draw": 0.0, "away": 0.0}

    for p in home_away.get("home_pairs", []):
        favor = p.get("favor")
        if favor in ("h0", "h1", "h2", "h3"):
            white["home"] += 0.5 * w_hg
        elif favor in ("tie",):
            white["draw"] += 0.4 * w_hg
            white["home"] += 0.3 * w_hg

    for p in home_away.get("away_pairs", []):
        favor = p.get("favor")
        if favor in ("a0", "a1", "a2", "a3"):
            white["away"] += 0.5 * w_ag
        elif favor in ("tie",):
            white["draw"] += 0.4 * w_ag
            white["away"] += 0.3 * w_ag

    for b in total_ballast.get("bucket_pairs", []):
        favor = b.get("favor")
        if favor in ("g0", "g1", "g2", "g3", "g4", "g5"):
            gv = int(str(favor)[1:])
            if gv <= 1:
                white["draw"] += 0.3 * w_tb
            elif gv == 2:
                white["draw"] += 0.4 * w_tb
                white["home"] += 0.2 * w_tb
                white["away"] += 0.2 * w_tb
            else:
                white["home"] += 0.2 * w_tb
                white["away"] += 0.2 * w_tb

    for tv in total_ballast.get("ttg_vs_crs", []):
        g = int(tv.get("g") or 0)
        tag = tv.get("tag")
        if tag == "strong":
            if g <= 1:
                white["draw"] += 0.3 * w_tb
            elif g == 2:
                white["draw"] += 0.35 * w_tb
                white["home"] += 0.15 * w_tb
                white["away"] += 0.15 * w_tb
        elif tag == "weak":
            if g >= 4:
                black["draw"] += 0.2 * w_tb

    for p in pairs:
        favor = p.get("favor")
        label = str(p.get("label") or "")
        if favor in ("tie", "1-1", "0-0", "2-2", "3-3"):
            white["draw"] += 0.35 * w_cp
            if "主队" in label or "2-1 vs 1-0" in label:
                white["home"] += 0.15 * w_cp
            elif "客队" in label or "1-2 vs 0-1" in label:
                white["away"] += 0.15 * w_cp
        elif favor in ("2-0", "1-0", "2-1", "3-1", "3-2"):
            white["home"] += 0.45 * w_cp
        elif favor in ("0-2", "0-1", "1-2", "1-3", "2-3"):
            white["away"] += 0.45 * w_cp

    if handicap.get("home_cover_less_likely"):
        black["home"] += 1.5 * w_hh
        white["draw"] += 0.8 * w_hh
        white["away"] += 0.6 * w_hh
    if handicap.get("away_cover_less_likely"):
        black["away"] += 1.5 * w_hh
        white["draw"] += 0.8 * w_hh
        white["home"] += 0.6 * w_hh
    if handicap.get("draw_anchor_same_as_handicap"):
        white["draw"] += 1.2 * w_hh

    di_tag = draw_impl.get("tag")
    if di_tag == "fake_draw_low":
        black["draw"] += 2.0 * w_di
    elif di_tag == "true_draw_high":
        white["draw"] += 1.8 * w_di
    elif di_tag == "core_draw_high":
        white["draw"] += 0.6 * w_di

    # ===== 方案 B-1：HHAD-pref 赔率绝对值分桶 白/黑名单 =====
    hhp = hhad_sig.get("pref")
    hhp_bucket = hhad_sig.get("pref_bucket")
    if hhp in ("home", "draw", "away") and isinstance(hhp_bucket, str):
        if hhp_bucket in ("sweet_low", "sweet_mid", "sweet_high"):
            white[hhp] += 1.2 * w_hp
        elif hhp_bucket == "ultra_low":
            black[hhp] += 1.0 * w_hp
        elif hhp_bucket == "weak_high":
            black[hhp] += 0.6 * w_hp

    # ===== 方案 B-2：HHAD 三项赔率 Spread 分桶 =====
    hh_sp = hhad_sig.get("spread_bucket")
    if isinstance(hh_sp, str):
        if hh_sp == "close":
            pass
        elif hh_sp == "wide":
            _hp = _had_preference(had)
            if _hp in ("home", "draw", "away"):
                black[_hp] += 0.6 * w_hs

    # ===== 方案 B-3：HAD×HHAD 同向主让 / 反向客让 一致性信号 =====
    had_pref_s = _had_preference(had)
    hhad_pref_s = hhad_sig.get("pref")
    if had_pref_s == "home" and hhad_pref_s == "home":
        black["away"] += 0.6 * w_cons
        black["draw"] += 0.4 * w_cons
        white["home"] += 0.4 * w_cons
    elif had_pref_s == "away" and hhad_pref_s == "home":
        white["home"] += 0.5 * w_cons
    elif had_pref_s == "home" and hhad_pref_s == "away":
        white["away"] += 0.5 * w_cons

    had_pref = had_pref_s
    if had_pref in ("home", "draw", "away"):
        base_protect = float(w_protect)
        black_hp = float(black.get(had_pref, 0.0))
        white_hp = float(white.get(had_pref, 0.0))
        gap_to_close = max(0.0, black_hp - white_hp)
        extra_protect = gap_to_close + 1.5 * float(w_protect)
        white[had_pref] += base_protect + extra_protect
        protect_detail: dict[str, float] = {
            "base": base_protect,
            "gap_to_close": float(gap_to_close),
            "extra": float(extra_protect),
            "total": float(base_protect + extra_protect),
        }
    else:
        protect_detail = {}

    scores = {k: float(black.get(k, 0.0) - white.get(k, 0.0)) for k in ("home", "draw", "away")}
    detail: dict[str, Any] = {
        "home_away_goals": home_away,
        "total_ballast": total_ballast,
        "crs_pairs": pairs,
        "handicap": handicap,
        "draw_implied": draw_impl,
        "hhad_sig": hhad_sig,
        "black": {k: float(v) for k, v in black.items()},
        "white": {k: float(v) for k, v in white.items()},
        "had_pref": had_pref,
        "protect_detail": protect_detail,
    }
    return scores, detail


TUNED7_0821_ANCHORS_V1: dict[str, Any] = {
    "pool_topk": 15,
    "pool_min_pool": 10,
    "pool_gap_abs": 5.0,
    "pool_gap_ratio": 1.35,
    "outcome_exclude_min_evidence": 3,
    "exclude_draw_had_ratio_ge": 1.75,
    "exclude_side_had_ratio_ge": 1.75,
    "exclude_side_base_ratio_ge": 1.75,
    "use_hhad_preference": True,
    "outcome_enforce_exclude_min_votes": 1,
    "outcome_enforce_strategy": "anchors_v1",
    "outcome_enforce_anchor_weights": {
        "home_goals": 1.0,
        "away_goals": 1.0,
        "ttg_ballast": 1.0,
        "crs_pairs": 1.0,
        "had_hhad": 1.5,
        "draw_implied": 1.5,
        "had_pref_protect": 2.5,
        "hhad_pref_bucket": 1.0,
        "hhad_spread": 1.0,
        "hhad_consensus": 1.0,
    },
}


@dataclass
class MarketFlowV2Config:
    pool_topk: int = 15
    pool_min_pool: int = 10
    pool_gap_abs: float = 5.0
    pool_gap_ratio: float = 1.35
    base_scores: list[str] = field(default_factory=lambda: ["1-0", "2-0", "0-1", "0-2", "0-0", "1-1"])

    outcome_exclude_min_evidence: int = 3
    exclude_draw_had_ratio_ge: float = 1.75
    exclude_side_had_ratio_ge: float = 1.75
    exclude_side_base_ratio_ge: float = 1.75
    use_hhad_preference: bool = True
    outcome_enforce_exclude_min_votes: int = 1
    outcome_enforce_strategy: str = "anchors_v1"
    outcome_enforce_anchor_weights: dict[str, float] = field(
        default_factory=lambda: {
            "home_goals": 1.0,
            "away_goals": 1.0,
            "ttg_ballast": 1.0,
            "crs_pairs": 1.0,
            "had_hhad": 1.5,
            "draw_implied": 1.5,
            "had_pref_protect": 2.5,
        }
    )

    induce_enable: bool = True
    induce_ttg_close_abs_le: float = 0.25
    induce_ttg_close_ratio_le: float = 1.08
    induce_crs_reverse_abs_ge: float = 0.8
    induce_crs_reverse_ratio_ge: float = 1.15
    induce_no_candidate_ttg_odd_le: float | None = None

    goals_sort_order: list[str] = field(default_factory=lambda: ["ttg_odd", "crs_best_odd", "goals_freq", "style_rank"])
    low_goals_freq_bonus_0: float = 0.0
    low_goals_freq_bonus_1: float = 0.0

    score_pick_use_ability_rules: bool = True
    ability_away2_ratio_le: float = 1.35
    ability_home2_ratio_le: float = 1.35
    ability_draw_ratio_le: float = 1.35

    style_map: dict[str, str] = field(
        default_factory=lambda: {
            "防守": "防守型",
            "守强": "防守型",
            "大开大合": "大开大合",
            "双弱": "大开大合",
            "守差": "大开大合",
            "攻强": "均衡",
            "攻守俱佳": "均衡",
            "均衡": "均衡",
        }
    )

    @staticmethod
    def from_dict(d: dict) -> "MarketFlowV2Config":
        if not isinstance(d, dict):
            return MarketFlowV2Config()
        base = MarketFlowV2Config()
        valid_fields = set(base.__dict__.keys())
        kwargs: dict[str, Any] = {}
        for k in valid_fields:
            if k in d:
                kwargs[k] = d[k]
        if "base_scores" in kwargs:
            if isinstance(kwargs["base_scores"], list):
                kwargs["base_scores"] = [str(x) for x in kwargs["base_scores"] if isinstance(x, (str, int, float))]
            else:
                kwargs["base_scores"] = list(base.base_scores)
        if "goals_sort_order" in kwargs:
            if isinstance(kwargs["goals_sort_order"], list):
                kwargs["goals_sort_order"] = [str(x) for x in kwargs["goals_sort_order"] if isinstance(x, str)]
            else:
                kwargs["goals_sort_order"] = list(base.goals_sort_order)
        if "style_map" in kwargs:
            if isinstance(kwargs["style_map"], dict):
                kwargs["style_map"] = {str(k): str(v) for k, v in kwargs["style_map"].items()}
            else:
                kwargs["style_map"] = dict(base.style_map)
        if "outcome_enforce_anchor_weights" in kwargs:
            cleaned = {}
            val = kwargs["outcome_enforce_anchor_weights"]
            if isinstance(val, dict):
                for k, v in val.items():
                    if isinstance(k, str) and isinstance(v, (int, float)):
                        cleaned[str(k)] = float(v)
            if cleaned:
                kwargs["outcome_enforce_anchor_weights"] = cleaned
            else:
                kwargs["outcome_enforce_anchor_weights"] = {str(k): float(v) for k, v in base.outcome_enforce_anchor_weights.items()}
        if "outcome_enforce_strategy" in kwargs:
            s = str(kwargs["outcome_enforce_strategy"] or "").strip() or "confidence_ratio"
            kwargs["outcome_enforce_strategy"] = s
        return MarketFlowV2Config(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_topk": self.pool_topk,
            "pool_min_pool": self.pool_min_pool,
            "pool_gap_abs": self.pool_gap_abs,
            "pool_gap_ratio": self.pool_gap_ratio,
            "base_scores": list(self.base_scores),
            "outcome_exclude_min_evidence": self.outcome_exclude_min_evidence,
            "exclude_draw_had_ratio_ge": self.exclude_draw_had_ratio_ge,
            "exclude_side_had_ratio_ge": self.exclude_side_had_ratio_ge,
            "exclude_side_base_ratio_ge": self.exclude_side_base_ratio_ge,
            "use_hhad_preference": self.use_hhad_preference,
            "outcome_enforce_exclude_min_votes": int(self.outcome_enforce_exclude_min_votes or 0),
            "outcome_enforce_strategy": str(self.outcome_enforce_strategy or "confidence_ratio"),
            "outcome_enforce_anchor_weights": {str(k): float(v) for k, v in (self.outcome_enforce_anchor_weights or {}).items()},
            "induce_enable": self.induce_enable,
            "induce_ttg_close_abs_le": self.induce_ttg_close_abs_le,
            "induce_ttg_close_ratio_le": self.induce_ttg_close_ratio_le,
            "induce_crs_reverse_abs_ge": self.induce_crs_reverse_abs_ge,
            "induce_crs_reverse_ratio_ge": self.induce_crs_reverse_ratio_ge,
            "induce_no_candidate_ttg_odd_le": self.induce_no_candidate_ttg_odd_le,
            "goals_sort_order": list(self.goals_sort_order),
            "low_goals_freq_bonus_0": float(self.low_goals_freq_bonus_0 or 0.0),
            "low_goals_freq_bonus_1": float(self.low_goals_freq_bonus_1 or 0.0),
            "score_pick_use_ability_rules": self.score_pick_use_ability_rules,
            "ability_away2_ratio_le": self.ability_away2_ratio_le,
            "ability_home2_ratio_le": self.ability_home2_ratio_le,
            "ability_draw_ratio_le": self.ability_draw_ratio_le,
            "style_map": dict(self.style_map),
        }


def _safe_ratio(a: float | None, b: float | None) -> float | None:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return None
    if float(a) <= 0 or float(b) <= 0:
        return None
    return float(a) / float(b)


def _normalize_style_tag_v2(style_tag: str | None, cfg: MarketFlowV2Config) -> str:
    raw = str(style_tag or "").strip()
    if not raw:
        return "均衡"
    for k, v in cfg.style_map.items():
        if k and k in raw:
            return str(v)
    return "均衡"


def _matchup_style_v2(home_style_tag: str | None, away_style_tag: str | None, cfg: MarketFlowV2Config) -> str:
    hs = _normalize_style_tag_v2(home_style_tag, cfg)
    as_ = _normalize_style_tag_v2(away_style_tag, cfg)
    return _matchup_style(hs, as_)


def _base_outcome_reference_odds(base_odds: dict[str, float | None]) -> dict[str, float | None]:
    def _min_valid(*vals: float | None) -> float | None:
        xs = [float(v) for v in vals if isinstance(v, (int, float)) and float(v) > 0]
        return min(xs) if xs else None

    return {
        "home": _min_valid(base_odds.get("1-0"), base_odds.get("2-0")),
        "draw": _min_valid(base_odds.get("0-0"), base_odds.get("1-1")),
        "away": _min_valid(base_odds.get("0-1"), base_odds.get("0-2")),
    }


def _build_outcome_constraint_v2(
    had: dict[str, float],
    hhad: dict[str, float],
    base_odds: dict[str, float | None],
    cfg: MarketFlowV2Config,
    crs: dict[str, float] | None = None,
    ttg: Any = None,
) -> tuple[set[str], list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    enforce_trace: list[dict[str, Any]] = []
    _anchor_detail_full: dict[str, Any] = {}

    had_pref = _had_preference(had)
    hhad_pref = _hhad_preference(hhad) if cfg.use_hhad_preference else None

    outcome_base = _base_outcome_reference_odds(base_odds)
    home_base = outcome_base["home"]
    away_base = outcome_base["away"]
    draw_base = outcome_base["draw"]

    allowed: set[str] = {"home", "draw", "away"}
    votes = {"home": 0, "draw": 0, "away": 0}
    confidence_ratios: dict[str, float] = {"home": 0.0, "draw": 0.0, "away": 0.0}

    def _record_confidence(target: str, ratio: float | None) -> None:
        if isinstance(ratio, (int, float)) and float(ratio) > float(confidence_ratios.get(target) or 0.0):
            confidence_ratios[target] = float(ratio)

    def _add(
        target_outcome: str,
        metric: str,
        lhs: str,
        rhs: str,
        value: Any,
        threshold: float | None,
        passed: bool,
    ) -> None:
        evidence.append(
            {
                "target_outcome": target_outcome,
                "metric": metric,
                "lhs": lhs,
                "rhs": rhs,
                "value": value,
                "threshold": threshold,
                "pass": bool(passed),
                "kind": "metric",
            }
        )
        if passed and target_outcome in votes:
            votes[target_outcome] += 1

    if draw_base is not None and home_base is not None:
        r = _safe_ratio(float(draw_base), float(home_base))
        passed = (r is not None) and (r >= float(cfg.exclude_draw_had_ratio_ge))
        _add("draw", "base_ratio", "draw_base", "home_base", r, float(cfg.exclude_draw_had_ratio_ge), passed)
        _record_confidence("draw", r)

    if draw_base is not None and away_base is not None:
        r = _safe_ratio(float(draw_base), float(away_base))
        passed = (r is not None) and (r >= float(cfg.exclude_draw_had_ratio_ge))
        _add("draw", "base_ratio", "draw_base", "away_base", r, float(cfg.exclude_draw_had_ratio_ge), passed)
        _record_confidence("draw", r)

    had_draw = had.get("draw")
    if isinstance(had_draw, (int, float)) and had_pref in {"home", "away"}:
        had_side = had.get(had_pref)
        r = _safe_ratio(float(had_draw), float(had_side) if isinstance(had_side, (int, float)) else None)
        passed = (r is not None) and (r >= float(cfg.exclude_draw_had_ratio_ge))
        _add("draw", "had_ratio", "had_draw", f"had_{had_pref}", r, float(cfg.exclude_draw_had_ratio_ge), passed)
        _record_confidence("draw", r)

    if had_pref in {"home", "away"} and hhad_pref == had_pref:
        _add("draw", "hhad_pref", "hhad_pref", f"support_{had_pref}", had_pref, None, True)

    if votes["draw"] >= int(cfg.outcome_exclude_min_evidence):
        allowed.discard("draw")

    if had_pref == "home":
        had_a = had.get("away")
        had_h = had.get("home")
        r = _safe_ratio(float(had_a) if isinstance(had_a, (int, float)) else None, float(had_h) if isinstance(had_h, (int, float)) else None)
        passed = (r is not None) and (r >= float(cfg.exclude_side_had_ratio_ge))
        _add("away", "had_ratio", "had_away", "had_home", r, float(cfg.exclude_side_had_ratio_ge), passed)
        _record_confidence("away", r)

        if away_base is not None and home_base is not None:
            base_r = _safe_ratio(float(away_base), float(home_base))
            base_passed = (base_r is not None) and (base_r >= float(cfg.exclude_side_base_ratio_ge))
            _add("away", "base_ratio", "away_base", "home_base", base_r, float(cfg.exclude_side_base_ratio_ge), base_passed)
            _record_confidence("away", base_r)

        if hhad_pref == "home":
            _add("away", "hhad_pref", "hhad_pref", "support_home", hhad_pref, None, True)

        if votes["away"] >= int(cfg.outcome_exclude_min_evidence):
            allowed.discard("away")

    if had_pref == "away":
        had_a = had.get("away")
        had_h = had.get("home")
        r = _safe_ratio(float(had_h) if isinstance(had_h, (int, float)) else None, float(had_a) if isinstance(had_a, (int, float)) else None)
        passed = (r is not None) and (r >= float(cfg.exclude_side_had_ratio_ge))
        _add("home", "had_ratio", "had_home", "had_away", r, float(cfg.exclude_side_had_ratio_ge), passed)
        _record_confidence("home", r)

        if home_base is not None and away_base is not None:
            base_r = _safe_ratio(float(home_base), float(away_base))
            base_passed = (base_r is not None) and (base_r >= float(cfg.exclude_side_base_ratio_ge))
            _add("home", "base_ratio", "home_base", "away_base", base_r, float(cfg.exclude_side_base_ratio_ge), base_passed)
            _record_confidence("home", base_r)

        if hhad_pref == "away":
            _add("home", "hhad_pref", "hhad_pref", "support_away", hhad_pref, None, True)

        if votes["home"] >= int(cfg.outcome_exclude_min_evidence):
            allowed.discard("home")

    enforce_min = int(cfg.outcome_enforce_exclude_min_votes or 0)
    strategy = str(cfg.outcome_enforce_strategy or "confidence_ratio").strip().lower() or "confidence_ratio"
    anchor_scores: dict[str, float] = {"home": 0.0, "draw": 0.0, "away": 0.0}
    anchor_detail: dict[str, Any] = {}
    if strategy.startswith("anchor"):
        anchor_scores, anchor_detail = _compute_anchor_enforce_scores(had, hhad, crs, ttg, cfg)
        _anchor_detail_full.clear()
        _anchor_detail_full.update(anchor_detail or {})
    if enforce_min > 0:
        need_exclude_count = enforce_min - (3 - len(allowed))
        if need_exclude_count > 0:
            if strategy.startswith("anchor"):
                raw_remaining = [x for x in ["home", "draw", "away"] if x in allowed]
                raw_remaining_sorted_worst_first = sorted(
                    raw_remaining,
                    key=lambda k: (float(anchor_scores.get(k) or 0.0), k),
                )
                remaining = []
                had_pref_protect = _had_preference(had)
                protected: set[str] = set()
                for out in raw_remaining_sorted_worst_first:
                    if had_pref_protect == out and bool(anchor_detail.get("white", {}).get(out, 0.0) + 0.001 > anchor_detail.get("black", {}).get(out, 0.0)):
                        protected.add(out)
                        continue
                    remaining.append(out)
                for out in raw_remaining_sorted_worst_first:
                    if out not in remaining:
                        remaining.append(out)
            else:
                remaining = sorted(
                    [x for x in ["home", "draw", "away"] if x in allowed],
                    key=lambda k: (float(confidence_ratios.get(k) or 0.0), k),
                )
            forced = []
            for out in remaining:
                if len(forced) >= need_exclude_count:
                    break
                if out not in allowed:
                    continue
                allowed.discard(out)
                votes[out] = int(votes.get(out) or 0) + 1
                forced.append(out)
                if strategy.startswith("anchor"):
                    evidence.append(
                        {
                            "target_outcome": out,
                            "metric": "enforce_min_votes_anchors",
                            "lhs": "anchor_score",
                            "rhs": "anchor_whitelist_blacklist",
                            "value": {
                                "strategy": strategy,
                                "needed_before": need_exclude_count,
                                "anchor_score_order": list(remaining),
                                "anchor_scores": {kk: float(vv) for kk, vv in (anchor_scores or {}).items()},
                                "anchor_detail": {
                                    "black": anchor_detail.get("black"),
                                    "white": anchor_detail.get("white"),
                                    "handicap": anchor_detail.get("handicap"),
                                    "draw_implied": anchor_detail.get("draw_implied"),
                                    "had_pref": anchor_detail.get("had_pref"),
                                },
                                "picked_score": float(anchor_scores.get(out) or 0.0),
                            },
                            "threshold": int(cfg.outcome_exclude_min_evidence),
                            "pass": True,
                            "kind": "enforce",
                        }
                    )
                    enforce_trace.append(
                        {
                            "target_outcome": out,
                            "strategy": strategy,
                            "anchor_score": float(anchor_scores.get(out) or 0.0),
                            "anchor_order": list(remaining),
                            "anchor_scores": {kk: float(vv) for kk, vv in (anchor_scores or {}).items()},
                            "anchor_white": anchor_detail.get("white"),
                            "anchor_black": anchor_detail.get("black"),
                        }
                    )
                else:
                    evidence.append(
                        {
                            "target_outcome": out,
                            "metric": "enforce_min_votes",
                            "lhs": "need_exclude_count",
                            "rhs": "confidence_ratio",
                            "value": {
                                "needed_before": need_exclude_count,
                                "confidence_ratio_order": list(remaining),
                                "picked_confidence_ratio": float(confidence_ratios.get(out) or 0.0),
                            },
                            "threshold": int(cfg.outcome_exclude_min_evidence),
                            "pass": True,
                            "kind": "enforce",
                        }
                    )
                    enforce_trace.append(
                        {
                            "target_outcome": out,
                            "confidence_ratio": float(confidence_ratios.get(out) or 0.0),
                            "confidence_order": list(remaining),
                        }
                    )

    # ===== 公共变量：had_draw 提前初始化，TOP3 需要先于 #4 判断 =====
    had_draw = had.get("draw") if isinstance(had, dict) else None

    # ===== TOP3强制回平局：[2.00,2.35)×一球盘 n=1702 24%样本 p_hit=41.42% -10.8pp 最大瓶颈 =====
    # 事实数据：该桶真实平局率=27.3%（464/1702），但模型选draw=0%，anchors keep_two={H,A}直接丢掉平局命中可能性
    # 触发条件：一球盘 ∧ keep_two={H,A} ∧ had_draw∈[2.90,3.40] ∧ had_pref赔率∈[2.00,2.35)
    # 注意：本段必须放在 #4 平局中心赔率保留段之前执行，否则 #4 的 [2.9,3.6] 更宽区间先加回draw导致本段条件 "draw not in allowed" 永False
    hhad_line_raw = hhad.get("line") if isinstance(hhad, dict) else None
    _is_one_ball_top3 = False
    if isinstance(hhad_line_raw, (int, float)):
        _abs_line = abs(float(hhad_line_raw))
        if 0.90 <= _abs_line <= 1.10:
            _is_one_ball_top3 = True
    if _is_one_ball_top3 and isinstance(had_draw, (int, float)) and "draw" not in allowed:
        if int(votes.get("draw") or 0) >= int(cfg.outcome_exclude_min_evidence):
            if 2.90 <= float(had_draw) <= 3.40:
                _side_odds = None
                if had_pref in ("home", "away"):
                    _s = had.get(had_pref) if isinstance(had, dict) else None
                    if isinstance(_s, (int, float)):
                        _side_odds = float(_s)
                if _side_odds is not None and 2.00 <= _side_odds < 2.35:
                    allowed.add("draw")
                    votes["draw"] = int(votes.get("draw") or 0) + 1
                    evidence.append(
                        {
                            "target_outcome": "draw",
                            "metric": "enforce_keep_draw_top3_oneball_bucket",
                            "lhs": "had_draw_odds_and_had_pref_odds_and_votes_draw_ge_min_evidence",
                            "rhs": "draw[2.90,3.40] ∩ pref[2.00,2.35) ∩ 一球盘 ∩ keep_two={H,A} ∩ votes_draw≥min_evidence",
                            "value": {"had_draw": float(had_draw), "had_pref": had_pref, "had_pref_odds": _side_odds, "hhad_line": float(hhad_line_raw), "votes_draw": int(votes.get("draw") or 0), "min_evidence": int(cfg.outcome_exclude_min_evidence)},
                            "threshold": None,
                            "pass": True,
                            "kind": "enforce_revert_top3",
                        }
                    )
                    enforce_trace.append(
                        {
                            "target_outcome": "draw",
                            "strategy": "draw_top3_oneball_mid_odds_bucket",
                            "anchor_draw_odds": float(had_draw),
                            "anchor_had_pref_odds": _side_odds,
                            "anchor_had_pref": had_pref,
                            "anchor_order": list(allowed),
                            "reason": "[2.00,2.35)×一球盘 n=1702 真实平局率27.3% 模型选draw=0% keep_two={H,A}直接丢失命中可能性 (仅当votes_draw>=min_evidence时生效)",
                        }
                    )

    # ===== 平局强制保留：事实数据 draw_missed 612/1493=41%，过度剔除的头号方向 =====
    # 当 D 实际赔率在 [2.90, 3.60]（平局概率最高的中心赔率区），但被 anchors 踢了 → 强制保留
    # 注：本段为宽门兜底，TOP3 窄门已经处理的 [2.9,3.4] + 一球盘 + [2.0,2.35) 段不重复进入本段（因为 draw 已在 allowed）
    if "draw" not in allowed and isinstance(had_draw, (int, float)):
        if int(votes.get("draw") or 0) >= int(cfg.outcome_exclude_min_evidence):
            if 2.90 <= float(had_draw) <= 3.60:
                allowed.add("draw")
                votes["draw"] = int(votes.get("draw") or 0)
                evidence.append(
                    {
                        "target_outcome": "draw",
                        "metric": "enforce_keep_draw_center_odds",
                        "lhs": "had_draw_odds_and_votes_draw_ge_min_evidence",
                        "rhs": "draw[2.90,3.60] ∩ votes_draw≥min_evidence",
                        "value": {"had_draw": float(had_draw), "votes_draw": int(votes.get("draw") or 0), "min_evidence": int(cfg.outcome_exclude_min_evidence)},
                        "threshold": None,
                        "pass": True,
                        "kind": "enforce_revert",
                    }
                )
                enforce_trace.append(
                    {
                        "target_outcome": "draw",
                        "strategy": "draw_center_odds_safety",
                        "anchor_draw_odds": float(had_draw),
                        "anchor_order": list(allowed),
                        "reason": "draw_missed 612/1493=41% 平局过度剔除最严重，保留中心赔率段 (仅当votes_draw>=min_evidence时生效)",
                    }
                )

    out_enforce: list[dict[str, Any]] = []
    if _anchor_detail_full:
        out_enforce.append({"__kind__": "anchor_detail_full", "detail": _anchor_detail_full})
    out_enforce.extend(list(enforce_trace))
    return allowed, evidence, {k: int(v) for k, v in votes.items()}, out_enforce


class MarketFlowEngine:
    def predict(
        self,
        home_style_tag: str | None,
        away_style_tag: str | None,
        had: dict[str, float] | None,
        hhad: dict[str, float] | None,
        ttg: dict[str, float] | None,
        crs: dict[str, float] | None,
        topk: int = 6,
        min_pool: int = 4,
        gap_abs: float = 2.5,
        gap_ratio: float = 1.2,
    ) -> dict[str, Any]:
        had = had or {}
        hhad = hhad or {}
        ttg = ttg or {}
        crs = crs or {}

        crs_items = [(k, float(v)) for k, v in crs.items() if isinstance(v, (int, float)) and float(v) > 0]
        crs_items.sort(key=lambda x: x[1])

        odds_sorted = [o for _, o in crs_items]
        gap_idx = _detect_gap(odds_sorted, gap_abs=gap_abs, gap_ratio=gap_ratio) if odds_sorted else None

        if gap_idx is None:
            pool = crs_items[: max(0, int(topk))]
            pool_rule: dict[str, Any] = {"type": "topk", "k": int(topk)}
        else:
            pool = crs_items[: gap_idx + 1]
            pool_rule = {"type": "gap", "gap_idx": int(gap_idx), "gap_at": float(odds_sorted[gap_idx])}

        if len(pool) < int(min_pool):
            pool = crs_items[: int(min_pool)]

        pool_scores = [s for s, _ in pool]

        goals_freq: dict[int, int] = {}
        for s in pool_scores:
            parsed = _parse_score(s)
            if not parsed:
                continue
            g = parsed[0] + parsed[1]
            goals_freq[g] = goals_freq.get(g, 0) + 1

        def _as_num(v: float | None) -> float | None:
            if isinstance(v, (int, float)) and float(v) > 0:
                return float(v)
            return None

        def _min_odds(*vals: float | None) -> float | None:
            xs = [_as_num(v) for v in vals]
            ys = [x for x in xs if x is not None]
            return min(ys) if ys else None

        had_pref = _had_preference(had)
        hhad_pref = _hhad_preference(hhad)
        line_val: float | None = None
        line_raw = hhad.get("line")
        if isinstance(line_raw, (int, float)):
            line_val = float(line_raw)

        base_odds = {
            "1-0": _crs_odd(crs, "1-0"),
            "2-0": _crs_odd(crs, "2-0"),
            "0-1": _crs_odd(crs, "0-1"),
            "0-2": _crs_odd(crs, "0-2"),
            "0-0": _crs_odd(crs, "0-0"),
            "1-1": _crs_odd(crs, "1-1"),
        }
        home_base = _min_odds(base_odds.get("1-0"), base_odds.get("2-0"))
        away_base = _min_odds(base_odds.get("0-1"), base_odds.get("0-2"))
        draw_base = _min_odds(base_odds.get("0-0"), base_odds.get("1-1"))

        def _sorted_outcomes() -> list[str]:
            base_map = {"home": home_base, "draw": draw_base, "away": away_base}
            items = [(k, v) for k, v in base_map.items() if v is not None]
            items.sort(key=lambda x: x[1])
            out = [k for k, _ in items]
            for k in ["home", "draw", "away"]:
                if k not in out:
                    out.append(k)
            return out

        outcome_pref = _sorted_outcomes()

        exclude_outcomes: set[str] = set()
        if had_pref == "home" and home_base is not None and away_base is not None and home_base <= 10 and away_base >= 20:
            exclude_outcomes.add("away")
        if had_pref == "away" and home_base is not None and away_base is not None and away_base <= 10 and home_base >= 20:
            exclude_outcomes.add("home")

        matchup_style = _matchup_style(home_style_tag, away_style_tag)
        priorities = _style_priority(matchup_style)

        def _ttg_anchor() -> int | None:
            best: tuple[int, float] | None = None
            for k, v in ttg.items():
                if not isinstance(v, (int, float)) or float(v) <= 0:
                    continue
                if isinstance(k, int):
                    g = int(k)
                elif isinstance(k, str) and k.isdigit():
                    g = int(k)
                else:
                    continue
                if best is None or float(v) < best[1]:
                    best = (g, float(v))
            return best[0] if best else None

        g_anchor = _ttg_anchor()

        anchor_delta_abs = 0.8
        pool_min_odd = float(pool[0][1]) if pool else None
        anchor_scores = []
        if g_anchor is not None:
            for s, o in pool:
                parsed = _parse_score(s)
                if not parsed:
                    continue
                if parsed[0] + parsed[1] == int(g_anchor):
                    anchor_scores.append((s, float(o)))
        anchor_crs_min = min([o for _, o in anchor_scores], default=None)
        anchor_match = (
            g_anchor is not None
            and pool_min_odd is not None
            and anchor_crs_min is not None
            and float(anchor_crs_min) <= float(pool_min_odd) + float(anchor_delta_abs)
        )

        def _goal_sort_key(g: int) -> tuple[float, int, int]:
            odd = _ttg_get(ttg, g)
            return (
                float(odd) if odd is not None else 1e9,
                -int(goals_freq.get(g, 0)),
                int(priorities.index(g)) if g in priorities else 999,
            )

        all_goals = list(goals_freq.keys())
        all_goals.sort(key=_goal_sort_key)

        best_g: int | None = None
        second_g: int | None = None

        if anchor_match and g_anchor is not None:
            best_g = int(g_anchor)
            for g in all_goals:
                if g != best_g:
                    second_g = g
                    break
        else:
            top_pool = pool[:3]
            if top_pool:
                p0 = _parse_score(top_pool[0][0])
                if p0:
                    best_g = p0[0] + p0[1]
            if best_g is not None:
                for s, _ in top_pool[1:]:
                    ps = _parse_score(s)
                    if not ps:
                        continue
                    g = ps[0] + ps[1]
                    if g != best_g:
                        second_g = g
                        break
            if best_g is None and all_goals:
                best_g = all_goals[0]
            if second_g is None:
                for g in all_goals:
                    if best_g is None or g != best_g:
                        second_g = g
                        break

        def _score_rank(score: str) -> tuple[int, int, int, str]:
            p = _parse_score(score)
            if not p:
                return (999, 999, 999, score)
            out = _score_outcome(p[0], p[1])
            try:
                out_rank = outcome_pref.index(out)
            except ValueError:
                out_rank = 999
            had_rank = 0 if had_pref and out == had_pref else 1
            hhad_rank = 1
            if hhad_pref and out:
                res = _hhad_outcome(p[0], p[1], line_val)
                hhad_rank = 0 if res == hhad_pref else 1
            return (out_rank, had_rank, hhad_rank, score)

        def _pick_score_for_goals(g: int | None) -> tuple[str | None, dict[str, Any]]:
            meta: dict[str, Any] = {"goals": g}
            if g is None:
                meta["reason"] = "no_goals"
                return None, meta
            candidates = []
            for s, o in pool:
                p = _parse_score(s)
                if not p:
                    continue
                if p[0] + p[1] != int(g):
                    continue
                out = _score_outcome(p[0], p[1])
                candidates.append((s, float(o), out))
            meta["candidates_n"] = len(candidates)
            if not candidates:
                meta["reason"] = "no_candidates"
                return None, meta

            filtered = [c for c in candidates if c[2] not in exclude_outcomes] if exclude_outcomes else candidates
            meta["excluded_outcomes"] = sorted(list(exclude_outcomes)) if exclude_outcomes else []
            meta["filtered_n"] = len(filtered)
            if not filtered:
                filtered = candidates
                meta["filter_fallback"] = True
            else:
                meta["filter_fallback"] = False

            filtered.sort(key=lambda x: (x[1], _score_rank(x[0])))
            best = filtered[0][0]
            meta["picked"] = best
            meta["picked_odd"] = filtered[0][1]
            return best, meta

        best_score, best_score_meta = _pick_score_for_goals(best_g)
        second_score, second_score_meta = _pick_score_for_goals(second_g)
        if second_score == best_score and second_g is not None:
            alt = []
            for s, o in pool:
                p = _parse_score(s)
                if not p:
                    continue
                if p[0] + p[1] != int(second_g):
                    continue
                if s == best_score:
                    continue
                out = _score_outcome(p[0], p[1])
                alt.append((s, float(o), out))
            if alt:
                alt.sort(key=lambda x: (x[1], _score_rank(x[0])))
                second_score = alt[0][0]
                second_score_meta["picked_alt"] = second_score

        trace: dict[str, Any] = {
            "pool_rule": {**pool_rule, "min_pool": int(min_pool), "gap_abs": float(gap_abs), "gap_ratio": float(gap_ratio)},
            "pool_scores": pool_scores,
            "pool_scores_with_odds": [{"score": s, "odd": float(o)} for s, o in pool],
            "goals_freq": goals_freq,
            "matchup_style": matchup_style,
            "goal_priority": priorities,
            "ttg_anchor": {
                "g": g_anchor,
                "delta_abs": float(anchor_delta_abs),
                "pool_min_odd": pool_min_odd,
                "anchor_crs_min": anchor_crs_min,
                "anchor_gap": (float(anchor_crs_min) - float(pool_min_odd)) if (anchor_crs_min is not None and pool_min_odd is not None) else None,
                "match": bool(anchor_match),
                "anchor_scores": [{"score": s, "odd": o} for s, o in anchor_scores],
            },
            "direction": {
                "had_pref": had_pref,
                "hhad_pref": hhad_pref,
                "hhad_line": line_val,
                "base_odds": base_odds,
                "home_base": home_base,
                "draw_base": draw_base,
                "away_base": away_base,
                "outcome_pref": outcome_pref,
                "exclude_outcomes": sorted(list(exclude_outcomes)),
            },
            "selected_goals": {"best": best_g, "second": second_g},
            "selected_scores": {"best": best_score, "second": second_score},
            "score_pick": {"best": best_score_meta, "second": second_score_meta},
        }

        return {
            "best_total_goals": best_g,
            "second_total_goals": second_g,
            "best_score": best_score,
            "second_score": second_score,
            "trace": trace,
        }


class MarketFlowEngineV2:
    def predict(
        self,
        home_style_tag: str | None,
        away_style_tag: str | None,
        had: dict[str, float] | None,
        hhad: dict[str, float] | None,
        ttg: dict[str, float] | None,
        crs: dict[str, float] | None,
        config: MarketFlowV2Config | dict | None = None,
    ) -> dict[str, Any]:
        cfg = config if isinstance(config, MarketFlowV2Config) else MarketFlowV2Config.from_dict(config or {})
        had = had or {}
        hhad = hhad or {}
        ttg = ttg or {}
        crs = crs or {}

        crs_items = [(k, float(v)) for k, v in crs.items() if isinstance(v, (int, float)) and float(v) > 0]
        crs_items.sort(key=lambda x: x[1])
        odds_sorted = [o for _, o in crs_items]
        gap_idx = (
            _detect_gap(odds_sorted, gap_abs=float(cfg.pool_gap_abs), gap_ratio=float(cfg.pool_gap_ratio))
            if odds_sorted
            else None
        )

        if gap_idx is None:
            pool = crs_items[: max(0, int(cfg.pool_topk))]
            pool_rule: dict[str, Any] = {"type": "topk", "k": int(cfg.pool_topk)}
        else:
            pool = crs_items[: gap_idx + 1]
            pool_rule = {"type": "gap", "gap_idx": int(gap_idx), "gap_at": float(odds_sorted[gap_idx])}

        if len(pool) < int(cfg.pool_min_pool):
            pool = crs_items[: int(cfg.pool_min_pool)]

        base_scores_with_odds = []
        for s in cfg.base_scores:
            base_scores_with_odds.append({"score": str(s), "odd": _crs_odd(crs, str(s))})

        base_odds = {
            "1-0": _crs_odd(crs, "1-0"),
            "2-0": _crs_odd(crs, "2-0"),
            "0-1": _crs_odd(crs, "0-1"),
            "0-2": _crs_odd(crs, "0-2"),
            "0-0": _crs_odd(crs, "0-0"),
            "1-1": _crs_odd(crs, "1-1"),
        }

        normalized_home_style = _normalize_style_tag_v2(home_style_tag, cfg)
        normalized_away_style = _normalize_style_tag_v2(away_style_tag, cfg)
        matchup_style = _matchup_style_v2(home_style_tag, away_style_tag, cfg)
        priorities = _style_priority(matchup_style)

        allowed_outcomes, outcome_evidence, outcome_votes, outcome_enforce_raw = _build_outcome_constraint_v2(had, hhad, base_odds, cfg, crs=crs, ttg=ttg)
        no_had_mode = not bool(had)  # 竞彩不售胜平负（强弱悬殊场次）→ 进球盘口主导
        if no_had_mode:
            # 无 HAD 时方向无锚点：按方向锚点做强制剔除会把 CRS 热门池（强队大胜比分）整侧滤掉，
            # 导致所选总进球档候选比分为空 → best_score/second_score 空 → 整场预测失败（004/003/009/012 类）。
            # 放开 allowed，比分完全交给 TTG/CRS 进球链路：先由进球盘口定总进球，再在对应总进球档内按 CRS 隐含选分。
            allowed_outcomes = {"home", "draw", "away"}
            outcome_evidence = [{
                "target_outcome": None,
                "metric": "no_had_goals_driven",
                "lhs": "had",
                "rhs": "missing",
                "value": None,
                "threshold": None,
                "pass": True,
                "kind": "override",
            }]
            outcome_votes = {"home": 0, "draw": 0, "away": 0}
            outcome_enforce_raw = []
        anchor_detail_ex: dict[str, Any] = {}
        outcome_enforce: list[dict[str, Any]] = []
        for it in (outcome_enforce_raw or []):
            if isinstance(it, dict) and it.get("__kind__") == "anchor_detail_full":
                det = it.get("detail")
                if isinstance(det, dict):
                    anchor_detail_ex = det
            else:
                outcome_enforce.append(it)

        goals_freq: dict[int, int] = {}
        for s, _o in pool:
            parsed = _parse_score(s)
            if not parsed:
                continue
            g = parsed[0] + parsed[1]
            goals_freq[g] = goals_freq.get(g, 0) + 1

        buckets: dict[int, dict[str, Any]] = {}
        for s, o in pool:
            p = _parse_score(s)
            if not p:
                continue
            out = _score_outcome(p[0], p[1])
            if out not in allowed_outcomes:
                continue
            g = p[0] + p[1]
            b = buckets.get(g)
            if not b:
                b = {"candidates": []}
                buckets[g] = b
            b["candidates"].append({"score": s, "odd": float(o), "home_goals": int(p[0]), "away_goals": int(p[1]), "outcome": out})

        goals_buckets_payload: dict[str, Any] = {}
        for g in sorted(set(goals_freq.keys()) | set(buckets.keys())):
            cand = buckets.get(g, {}).get("candidates", [])
            odds = sorted([float(x["odd"]) for x in cand if isinstance(x, dict) and isinstance(x.get("odd"), (int, float))])
            crs_best_odd = min(odds) if odds else None
            crs_top2_mean = (sum(odds[:2]) / 2.0) if len(odds) >= 2 else None
            ttg_odd = _ttg_get(ttg, int(g))
            style_rank = priorities.index(int(g)) if int(g) in priorities else 999
            goals_buckets_payload[str(g)] = {
                "g": int(g),
                "goals_freq": int(goals_freq.get(g, 0)),
                "ttg_odd": ttg_odd,
                "n_candidates": int(len(cand)),
                "crs_best_odd": crs_best_odd,
                "crs_top2_mean": crs_top2_mean,
                "style_rank": int(style_rank),
                "candidates": list(cand),
            }

        induce_flags: dict[str, bool] = {}
        induce_reasons: dict[str, list[dict[str, Any]]] = {}

        if cfg.induce_enable:
            for g_str, b in goals_buckets_payload.items():
                ttg_odd = b.get("ttg_odd")
                n_candidates = b.get("n_candidates")
                if cfg.induce_no_candidate_ttg_odd_le is None:
                    continue
                if not isinstance(ttg_odd, (int, float)):
                    continue
                if int(n_candidates or 0) != 0:
                    continue
                if float(ttg_odd) <= float(cfg.induce_no_candidate_ttg_odd_le):
                    induce_flags[g_str] = True
                    induce_reasons[g_str] = [
                        {
                            "type": "no_candidate_after_filter",
                            "ttg_odd": float(ttg_odd),
                            "threshold": float(cfg.induce_no_candidate_ttg_odd_le),
                        }
                    ]

            ttg_goals: list[int] = []
            for k, v in ttg.items():
                if not isinstance(v, (int, float)) or float(v) <= 0:
                    continue
                if isinstance(k, int):
                    ttg_goals.append(int(k))
                    continue
                if isinstance(k, str) and k.isdigit():
                    ttg_goals.append(int(k))
            ttg_goals = sorted(set(ttg_goals), key=lambda g: _ttg_get(ttg, g) or 1e9)

            for i in range(len(ttg_goals) - 1):
                g0 = ttg_goals[i]
                g1 = ttg_goals[i + 1]
                o0 = _ttg_get(ttg, g0)
                o1 = _ttg_get(ttg, g1)
                if o0 is None or o1 is None:
                    continue
                abs_close = abs(float(o0) - float(o1)) <= float(cfg.induce_ttg_close_abs_le)
                ratio = (max(float(o0), float(o1)) / min(float(o0), float(o1))) if min(float(o0), float(o1)) > 0 else None
                ratio_close = (ratio is not None) and (ratio <= float(cfg.induce_ttg_close_ratio_le))
                if not (abs_close or ratio_close):
                    continue

                b0 = goals_buckets_payload.get(str(g0)) or {}
                b1 = goals_buckets_payload.get(str(g1)) or {}
                c0 = b0.get("crs_best_odd")
                c1 = b1.get("crs_best_odd")
                if not isinstance(c0, (int, float)) or not isinstance(c1, (int, float)):
                    continue
                abs_reverse = (float(c0) - float(c1)) >= float(cfg.induce_crs_reverse_abs_ge)
                ratio_reverse = (float(c0) / float(c1)) >= float(cfg.induce_crs_reverse_ratio_ge) if float(c1) > 0 else False
                if not (abs_reverse or ratio_reverse):
                    continue
                if str(g0) not in induce_flags:
                    induce_flags[str(g0)] = True
                    induce_reasons[str(g0)] = [
                        {
                            "type": "ttg_close_but_crs_reverse",
                            "g0": int(g0),
                            "g1": int(g1),
                            "ttg_o0": float(o0),
                            "ttg_o1": float(o1),
                            "ttg_abs_diff": abs(float(o0) - float(o1)),
                            "ttg_ratio": ratio,
                            "crs_best_odd_g0": float(c0),
                            "crs_best_odd_g1": float(c1),
                            "crs_abs_diff": float(c0) - float(c1),
                            "crs_ratio": (float(c0) / float(c1)) if float(c1) > 0 else None,
                            "thresholds": {
                                "ttg_close_abs_le": float(cfg.induce_ttg_close_abs_le),
                                "ttg_close_ratio_le": float(cfg.induce_ttg_close_ratio_le),
                                "crs_reverse_abs_ge": float(cfg.induce_crs_reverse_abs_ge),
                                "crs_reverse_ratio_ge": float(cfg.induce_crs_reverse_ratio_ge),
                            },
                        }
                    ]

        def _goals_sort_key(g: int) -> tuple:
            b = goals_buckets_payload.get(str(g)) or {}
            ttg_odd = b.get("ttg_odd")
            crs_best_odd = b.get("crs_best_odd")
            goals_freq = b.get("goals_freq")
            style_rank = b.get("style_rank")
            ttg_val = float(ttg_odd) if isinstance(ttg_odd, (int, float)) else 1e9
            if int(g) == 0:
                ttg_val += float(cfg.low_goals_freq_bonus_0 or 0.0)
            if int(g) == 1:
                ttg_val += float(cfg.low_goals_freq_bonus_1 or 0.0)
            mapping: dict[str, Any] = {
                "ttg_odd": ttg_val,
                "crs_best_odd": float(crs_best_odd) if isinstance(crs_best_odd, (int, float)) else 1e9,
                "goals_freq": -int(goals_freq) if isinstance(goals_freq, int) else 0,
                "style_rank": int(style_rank) if isinstance(style_rank, int) else 999,
            }
            return tuple(mapping.get(k, 1e9) for k in cfg.goals_sort_order)

        all_goals = sorted([int(g) for g in goals_buckets_payload.keys() if str(g).isdigit()])
        ranked_goals = sorted(all_goals, key=_goals_sort_key)

        ranked_goals_debug = []
        for g in ranked_goals:
            b = goals_buckets_payload.get(str(g)) or {}
            raw_ttg = float(b.get("ttg_odd")) if isinstance(b.get("ttg_odd"), (int, float)) else None
            bonus_used = 0.0
            if int(g) == 0:
                bonus_used = float(cfg.low_goals_freq_bonus_0 or 0.0)
            if int(g) == 1:
                bonus_used = float(cfg.low_goals_freq_bonus_1 or 0.0)
            ranked_goals_debug.append(
                {
                    "g": int(g),
                    "sort_key": list(_goals_sort_key(g)),
                    "ttg_odd": raw_ttg,
                    "ttg_odd_with_bonus": (float(raw_ttg) + bonus_used) if raw_ttg is not None else None,
                    "low_goals_bonus_used": bonus_used,
                    "crs_best_odd": b.get("crs_best_odd"),
                    "goals_freq": b.get("goals_freq"),
                    "style_rank": b.get("style_rank"),
                    "induce": bool(induce_flags.get(str(g)) is True),
                }
            )

        best_g = ranked_goals[0] if ranked_goals else None
        second_g = ranked_goals[1] if len(ranked_goals) >= 2 else None

        swap_meta: dict[str, Any] | None = None
        if cfg.induce_enable and best_g is not None and second_g is not None:
            if induce_flags.get(str(best_g)) is True and induce_flags.get(str(second_g)) is not True:
                best_g, second_g = second_g, best_g
                swap_meta = {"swapped": True, "reason": induce_reasons.get(str(second_g)) or []}
            else:
                swap_meta = {"swapped": False}

        def _ability() -> dict[str, Any]:
            away2_ratio = _safe_ratio(base_odds.get("0-2"), base_odds.get("0-1"))
            home2_ratio = _safe_ratio(base_odds.get("2-0"), base_odds.get("1-0"))
            draw_ratio = _safe_ratio(base_odds.get("1-1"), base_odds.get("0-0"))
            return {
                "away2_ratio": away2_ratio,
                "home2_ratio": home2_ratio,
                "draw_ratio": draw_ratio,
                "away2_ok": (away2_ratio <= float(cfg.ability_away2_ratio_le)) if away2_ratio is not None else None,
                "home2_ok": (home2_ratio <= float(cfg.ability_home2_ratio_le)) if home2_ratio is not None else None,
                "draw_ok": (draw_ratio <= float(cfg.ability_draw_ratio_le)) if draw_ratio is not None else None,
            }

        ability = _ability()

        def _pick_score_for_goals(g: int | None) -> tuple[str | None, dict[str, Any]]:
            if g is None:
                return None, {"goals": None, "candidates": [], "picked": None}
            b = goals_buckets_payload.get(str(g)) or {}
            cand_raw = b.get("candidates")
            candidates = cand_raw if isinstance(cand_raw, list) else []

            ranked: list[dict[str, Any]] = []
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                score = c.get("score")
                odd = c.get("odd")
                hg = c.get("home_goals")
                ag = c.get("away_goals")
                if not isinstance(score, str) or not isinstance(odd, (int, float)) or not isinstance(hg, int) or not isinstance(ag, int):
                    continue
                needs_away2 = (ag >= 2) and _score_outcome(hg, ag) == "away"
                needs_home2 = (hg >= 2) and _score_outcome(hg, ag) == "home"
                needs_draw = _score_outcome(hg, ag) == "draw"
                penalty = 0
                if cfg.score_pick_use_ability_rules:
                    if needs_away2 and ability.get("away2_ratio") is not None and ability.get("away2_ok") is False:
                        penalty += 1
                    if needs_home2 and ability.get("home2_ratio") is not None and ability.get("home2_ok") is False:
                        penalty += 1
                    if needs_draw and ability.get("draw_ratio") is not None and ability.get("draw_ok") is False:
                        penalty += 1
                ranked.append(
                    {
                        "score": score,
                        "odd": float(odd),
                        "penalty": int(penalty),
                        "needs": {"away2": bool(needs_away2), "home2": bool(needs_home2), "draw": bool(needs_draw)},
                        "rank_key": [float(odd), int(penalty), score],
                    }
                )
            ranked.sort(key=lambda x: (x["rank_key"][0], x["rank_key"][1], x["rank_key"][2]))
            picked = ranked[0]["score"] if ranked else None
            meta: dict[str, Any] = {
                "goals": int(g),
                "candidates": ranked,
                "picked": picked,
            }
            return picked, meta

        best_score, best_pick_meta = _pick_score_for_goals(best_g)
        second_score, second_pick_meta = _pick_score_for_goals(second_g)
        if best_score and second_score and best_score == second_score and second_g is not None:
            alt = [x for x in (second_pick_meta.get("candidates") or []) if isinstance(x, dict) and x.get("score") != best_score]
            if alt:
                second_score = alt[0].get("score")
                second_pick_meta["picked_alt"] = second_score

        trace: dict[str, Any] = {
            "version": "v2",
            "config": cfg.to_dict(),
            "inputs": {
                "home_style_tag": home_style_tag,
                "away_style_tag": away_style_tag,
                "had": dict(had),
                "hhad": dict(hhad),
                "ttg": dict(ttg),
                "crs": dict(crs),
            },
            "stage_a": {
                "pool_rule": {
                    **pool_rule,
                    "min_pool": int(cfg.pool_min_pool),
                    "gap_abs": float(cfg.pool_gap_abs),
                    "gap_ratio": float(cfg.pool_gap_ratio),
                },
                "pool_scores_with_odds": [{"score": s, "odd": float(o)} for s, o in pool],
                "base_scores_with_odds": base_scores_with_odds,
                "matchup_style": matchup_style,
                "goal_priority": priorities,
            },
            "stage_b": {
                "no_had_mode": bool(no_had_mode),
                "had_pref": _had_preference(had) if had else None,
                "allowed_outcomes": sorted(list(allowed_outcomes)),
                "excluded_outcomes": sorted([x for x in ["home", "draw", "away"] if x not in allowed_outcomes]),
                "evidence": outcome_evidence,
                "outcome_votes": outcome_votes,
                "enforce": outcome_enforce,
                "anchor_detail": anchor_detail_ex,
                "base_odds": base_odds,
            },
            "stage_c": {
                "goals_freq": goals_freq,
                "goals_buckets": goals_buckets_payload,
            },
            "stage_d": {
                "induce_flags": induce_flags,
                "induce_reasons": induce_reasons,
            },
            "stage_e": {
                "ranked_goals": ranked_goals_debug,
                "swap": swap_meta,
                "selected_goals": {"best": best_g, "second": second_g},
            },
            "stage_f": {
                "ability": ability,
                "best": best_pick_meta,
                "second": second_pick_meta,
            },
            "stage_g": {
                "home_style_tag_raw": home_style_tag,
                "away_style_tag_raw": away_style_tag,
                "home_style_tag_normalized": normalized_home_style,
                "away_style_tag_normalized": normalized_away_style,
                "matchup_style": matchup_style,
                "style_priority": priorities,
                "style_map": dict(cfg.style_map),
            },
        }

        return {
            "best_total_goals": best_g,
            "second_total_goals": second_g,
            "best_score": best_score or "",
            "second_score": second_score or "",
            "trace": trace,
        }
