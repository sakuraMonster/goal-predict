from __future__ import annotations

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

        matchup_style = _matchup_style(home_style_tag, away_style_tag)
        priorities = _style_priority(matchup_style)

        candidate_goals = [g for g in priorities if g in goals_freq]
        if not candidate_goals:
            candidate_goals = list(goals_freq.keys())

        def _goal_sort_key(g: int) -> tuple[float, int, int]:
            odd = _ttg_get(ttg, g)
            return (
                float(odd) if odd is not None else 1e9,
                -int(goals_freq.get(g, 0)),
                int(priorities.index(g)) if g in priorities else 999,
            )

        ttg_used = {str(g): _ttg_get(ttg, g) for g in candidate_goals}
        goal_sort_keys = {str(g): list(_goal_sort_key(g)) for g in candidate_goals}
        candidate_goals.sort(key=_goal_sort_key)
        best_g = candidate_goals[0] if len(candidate_goals) >= 1 else None
        second_g = candidate_goals[1] if len(candidate_goals) >= 2 else None

        def _best_score_for_goals(g: int) -> str | None:
            best: tuple[str, float] | None = None
            for score, odd in crs_items:
                parsed = _parse_score(score)
                if not parsed:
                    continue
                if parsed[0] + parsed[1] != g:
                    continue
                if best is None or odd < best[1]:
                    best = (score, odd)
            return best[0] if best else None

        def _second_score_for_goals(g: int) -> str | None:
            same = []
            for score, odd in crs_items:
                parsed = _parse_score(score)
                if not parsed:
                    continue
                if parsed[0] + parsed[1] != g:
                    continue
                same.append((score, odd))
            same.sort(key=lambda x: x[1])
            if len(same) >= 2:
                return same[1][0]
            return None

        best_score = _best_score_for_goals(best_g) if best_g is not None else None
        second_a = _second_score_for_goals(best_g) if best_g is not None else None
        second_b = _best_score_for_goals(second_g) if second_g is not None else None

        had_pref = _had_preference(had)
        hhad_pref = _hhad_preference(hhad)
        line_val: float | None = None
        line_raw = hhad.get("line")
        if isinstance(line_raw, (int, float)):
            line_val = float(line_raw)

        def _pick_second(s1: str | None, s2: str | None) -> tuple[str | None, dict[str, Any]]:
            meta: dict[str, Any] = {"candidates": {"a": s1, "b": s2}}
            if s1 and not s2:
                meta["reason"] = "only_a"
                return s1, meta
            if s2 and not s1:
                meta["reason"] = "only_b"
                return s2, meta
            if not s1 and not s2:
                meta["reason"] = "no_candidate"
                return None, meta
            if s1 == s2:
                meta["reason"] = "same_score"
                return s1, meta

            p1 = _parse_score(s1)
            p2 = _parse_score(s2)

            o_s1 = _crs_odd(crs, s1)
            o_s2 = _crs_odd(crs, s2)
            meta["crs_odds"] = {"a": o_s1, "b": o_s2}
            c1 = float(o_s1) if o_s1 is not None else 1e18
            c2 = float(o_s2) if o_s2 is not None else 1e18
            if c1 != c2:
                meta["reason"] = "crs_odds"
                return (s1 if c1 < c2 else s2), meta

            if had_pref and p1 and p2:
                out1 = _score_outcome(p1[0], p1[1])
                out2 = _score_outcome(p2[0], p2[1])
                if out1 == had_pref and out2 != had_pref:
                    meta["reason"] = "had_preference"
                    meta["had_pref"] = had_pref
                    return s1, meta
                if out2 == had_pref and out1 != had_pref:
                    meta["reason"] = "had_preference"
                    meta["had_pref"] = had_pref
                    return s2, meta

            if hhad_pref and p1 and p2:
                res1 = _hhad_outcome(p1[0], p1[1], line_val)
                res2 = _hhad_outcome(p2[0], p2[1], line_val)
                if res1 == hhad_pref and res2 != hhad_pref:
                    meta["reason"] = "hhad_preference"
                    meta["hhad_pref"] = hhad_pref
                    return s1, meta
                if res2 == hhad_pref and res1 != hhad_pref:
                    meta["reason"] = "hhad_preference"
                    meta["hhad_pref"] = hhad_pref
                    return s2, meta

            meta["reason"] = "fallback_a"
            return s1, meta

        second_score, second_meta = _pick_second(second_a, second_b)

        trace: dict[str, Any] = {
            "pool_rule": {**pool_rule, "min_pool": int(min_pool), "gap_abs": float(gap_abs), "gap_ratio": float(gap_ratio)},
            "pool_scores": pool_scores,
            "pool_scores_with_odds": [{"score": s, "odd": float(o)} for s, o in pool],
            "goals_freq": goals_freq,
            "matchup_style": matchup_style,
            "goal_priority": priorities,
            "candidate_goals": candidate_goals,
            "ttg_used": ttg_used,
            "goal_sort_keys": goal_sort_keys,
            "selected_goals": {"best": best_g, "second": second_g},
            "selected_scores": {"best": best_score, "second": second_score},
            "tiebreak": {
                "had_pref": had_pref,
                "hhad_pref": hhad_pref,
                "hhad_line": line_val,
                "second_pick": second_meta,
            },
        }

        return {
            "best_total_goals": best_g,
            "second_total_goals": second_g,
            "best_score": best_score,
            "second_score": second_score,
            "trace": trace,
        }
