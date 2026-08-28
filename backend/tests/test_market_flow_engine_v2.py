from app.predictor.models.market_flow import MarketFlowEngineV2


def test_market_flow_v2_trace_has_config_and_style_trace():
    engine = MarketFlowEngineV2()
    result = engine.predict(
        home_style_tag="双弱",
        away_style_tag="守差攻中",
        had={"home": 3.45, "draw": 3.16, "away": 1.91},
        hhad={"line": 1.0, "home": 1.68, "draw": 3.6, "away": 3.9},
        ttg={"0": 10.5, "1": 4.5, "2": 3, "3": 3.65, "4": 6.5, "5": 14},
        crs={"1-1": 6, "0-1": 7.5, "1-2": 7, "0-2": 9, "0-0": 10.5},
        config={"pool_topk": 3},
    )
    trace = result["trace"]
    assert trace["version"] == "v2"
    assert trace["config"]["pool_topk"] == 3
    assert "stage_a" in trace
    assert "pool_scores_with_odds" in trace["stage_a"]
    assert trace["stage_g"]["home_style_tag_normalized"] == "大开大合"
    assert trace["stage_g"]["away_style_tag_normalized"] == "大开大合"
    assert "outcome_votes" in trace["stage_b"]
    assert set(trace["stage_b"]["outcome_votes"].keys()) == {"home", "draw", "away"}
    assert "enforce" in trace["stage_b"]


def test_market_flow_v2_keeps_side_when_only_single_evidence_passes():
    engine = MarketFlowEngineV2()
    result = engine.predict(
        home_style_tag="双弱",
        away_style_tag="守差攻中",
        had={"home": 3.2, "draw": 3.4, "away": 1.8},
        hhad={"line": 1.0, "home": 1.68, "draw": 5.8, "away": 4.2},
        ttg={"0": 10.5, "1": 4.5, "2": 3, "3": 3.65, "4": 6.5, "5": 14},
        crs={
            "1-0": 10.6,
            "2-0": 16.5,
            "0-1": 7.9,
            "0-2": 8.0,
            "1-2": 7.0,
            "1-1": 6.4,
            "0-0": 10.5,
        },
        config={
            "outcome_exclude_min_evidence": 2,
            "exclude_side_had_ratio_ge": 1.6,
            "exclude_side_base_ratio_ge": 1.6,
            "outcome_enforce_exclude_min_votes": 0,
        },
    )
    allowed = set(result["trace"]["stage_b"]["allowed_outcomes"])
    assert "home" in allowed


def test_market_flow_v2_excludes_side_when_had_and_base_evidence_both_pass():
    engine = MarketFlowEngineV2()
    result = engine.predict(
        home_style_tag="双弱",
        away_style_tag="守差攻中",
        had={"home": 3.45, "draw": 3.16, "away": 1.91},
        hhad={"line": 1.0, "home": 1.68, "draw": 4.6, "away": 4.2},
        ttg={"0": 10.5, "1": 4.5, "2": 3, "3": 3.65, "4": 6.5, "5": 14},
        crs={
            "1-0": 12.5,
            "2-0": 18.0,
            "0-1": 7.5,
            "0-2": 9.0,
            "1-2": 7.0,
            "1-1": 6.0,
            "0-0": 10.5,
        },
        config={
            "outcome_exclude_min_evidence": 2,
            "exclude_side_had_ratio_ge": 1.6,
            "exclude_side_base_ratio_ge": 1.6,
            "exclude_draw_had_ratio_ge": 1.1,
        },
    )
    allowed = set(result["trace"]["stage_b"]["allowed_outcomes"])
    assert "home" not in allowed
    assert any(
        e["target_outcome"] == "home" and e["metric"] == "base_ratio" and e["pass"] is True
        for e in result["trace"]["stage_b"]["evidence"]
    )


def test_market_flow_v2_picks_scores_for_thu_004_sample():
    engine = MarketFlowEngineV2()
    result = engine.predict(
        home_style_tag="双弱",
        away_style_tag="守差攻中",
        had={"home": 3.45, "draw": 3.16, "away": 1.91},
        hhad={"line": 1.0, "home": 1.68, "draw": 3.6, "away": 3.9},
        ttg={"0": 10.5, "1": 4.5, "2": 3, "3": 3.65, "4": 6.5, "5": 14},
        crs={
            "1-0": 10.5,
            "2-0": 17,
            "2-1": 11,
            "0-0": 10.5,
            "1-1": 6,
            "0-1": 7.5,
            "0-2": 9,
            "1-2": 7,
            "0-3": 19,
            "1-3": 16,
        },
        config={
            "outcome_exclude_min_evidence": 1,
            "exclude_draw_had_ratio_ge": 1.1,
            "induce_ttg_close_abs_le": 1.0,
            "low_goals_freq_bonus_1": 0.0,
            "outcome_enforce_exclude_min_votes": 0,
            "pool_topk": 3,
        },
    )
    assert result["best_score"] == "1-2"
    assert result["second_score"] == "0-2"

    trace = result["trace"]
    assert trace["version"] == "v2"
    assert "stage_f" in trace
    assert isinstance(trace["stage_f"]["ability"], dict)
    assert trace["stage_e"]["selected_goals"]["best"] == 3
    assert trace["stage_d"]["induce_flags"].get("2") is True


def test_market_flow_v2_default_config_requires_3_evidence_to_exclude_side():
    engine = MarketFlowEngineV2()
    old_default_base_cfg = {
        "outcome_exclude_min_evidence": 3,
        "exclude_side_had_ratio_ge": 1.75,
        "exclude_side_base_ratio_ge": 1.75,
        "outcome_enforce_exclude_min_votes": 0,
    }
    result = engine.predict(
        home_style_tag="双弱",
        away_style_tag="守差攻中",
        had={"home": 4.0, "draw": 3.3, "away": 1.85},
        hhad={"line": -1.0, "home": 1.75, "draw": 4.4, "away": 3.2},
        ttg={"0": 10.5, "1": 4.5, "2": 3, "3": 3.65, "4": 6.5, "5": 14},
        crs={
            "1-0": 14.0,
            "2-0": 22.0,
            "0-1": 5.2,
            "0-2": 9.0,
            "1-2": 7.0,
            "1-1": 6.0,
            "0-0": 10.5,
            "2-1": 13.0,
        },
        config=old_default_base_cfg,
    )
    trace = result["trace"]
    assert trace["config"]["outcome_exclude_min_evidence"] == 3
    assert trace["config"]["exclude_side_had_ratio_ge"] == 1.75
    assert trace["config"]["exclude_side_base_ratio_ge"] == 1.75
    assert trace["config"]["outcome_enforce_exclude_min_votes"] == 0
    home_votes = sum(
        1 for e in trace["stage_b"]["evidence"] if e.get("target_outcome") == "home" and e.get("pass") is True
    )
    allowed = set(trace["stage_b"]["allowed_outcomes"])
    assert home_votes >= 2
    assert "home" in allowed
    assert len(trace["stage_b"]["excluded_outcomes"]) == 0 or trace["stage_b"]["outcome_votes"]["home"] < 3


def test_market_flow_v2_default_config_relaxes_pool_and_keeps_at_least_six_candidates():
    engine = MarketFlowEngineV2()
    old_pool_cfg = {
        "pool_topk": 10,
        "pool_min_pool": 8,
        "pool_gap_abs": 4.0,
        "pool_gap_ratio": 1.3,
    }
    crs = {
        f"{h}-{a}": round(4.0 + (h + a) * 0.6 + (h * 0.35) + (a * 0.2), 2)
        for h in range(0, 6)
        for a in range(0, 6)
        if abs(h - a) <= 4 and (h + a) <= 7
    }
    result = engine.predict(
        home_style_tag="双弱",
        away_style_tag="守差攻中",
        had={"home": 2.25, "draw": 3.3, "away": 3.1},
        hhad={"line": 0.0, "home": 1.95, "draw": 3.5, "away": 3.6},
        ttg={"0": 10.0, "1": 5.0, "2": 3.4, "3": 3.6, "4": 5.6, "5": 9.5, "6": 15.0, "7": 22.0},
        crs=crs,
        config=old_pool_cfg,
    )
    trace = result["trace"]
    assert trace["config"]["pool_topk"] == 10
    assert trace["config"]["pool_min_pool"] == 8
    assert trace["config"]["pool_gap_abs"] == 4.0
    assert trace["config"]["pool_gap_ratio"] == 1.3
    assert len(trace["stage_a"]["pool_scores_with_odds"]) >= 8


def test_market_flow_v2_default_config_bonus_lifts_g1_into_top2():
    engine = MarketFlowEngineV2()
    crs = {
        "0-0": 10.0,
        "1-0": 5.2,
        "2-0": 5.8,
        "2-1": 6.0,
        "1-1": 5.0,
        "0-1": 8.5,
        "0-2": 9.0,
        "1-2": 8.8,
        "2-2": 10.5,
        "3-0": 12.0,
        "3-1": 15.0,
    }
    result = engine.predict(
        home_style_tag="双弱",
        away_style_tag="守差攻中",
        had={"home": 2.1, "draw": 3.2, "away": 3.4},
        hhad={"line": 0.0, "home": 1.9, "draw": 3.4, "away": 3.7},
        ttg={"0": 9.5, "1": 4.8, "2": 3.6, "3": 3.9, "4": 5.5, "5": 9.0, "6": 14.5, "7": 21.0},
        crs=crs,
        config={"low_goals_freq_bonus_1": -1.0},
    )
    trace = result["trace"]
    assert trace["config"]["low_goals_freq_bonus_1"] == -1.0
    ranked = [row["g"] for row in trace["stage_e"]["ranked_goals"]]
    assert 1 in ranked[:2]
    sel = trace["stage_e"]["selected_goals"]
    assert sel["best"] == 1 or sel["second"] == 1
    g1_rows = [row for row in trace["stage_e"]["ranked_goals"] if row["g"] == 1]
    assert g1_rows and g1_rows[0].get("low_goals_bonus_used") == -1.0
    assert g1_rows[0].get("ttg_odd_with_bonus") is not None
    assert g1_rows[0]["ttg_odd_with_bonus"] < g1_rows[0]["ttg_odd"]


def test_market_flow_v2_enforce_min_votes_forces_at_least_one_excluded_outcome():
    engine = MarketFlowEngineV2()
    had = {"home": 2.6, "draw": 3.1, "away": 2.8}
    hhad = {"line": 0.0, "home": 2.45, "draw": 3.2, "away": 2.75}
    ttg = {"0": 10.0, "1": 4.7, "2": 3.3, "3": 3.7, "4": 5.5, "5": 9.0, "6": 14.0}
    crs = {
        "1-0": 6.8,
        "2-0": 9.2,
        "2-1": 7.1,
        "1-1": 6.0,
        "0-0": 10.0,
        "0-1": 7.2,
        "0-2": 9.5,
        "1-2": 7.4,
        "2-2": 10.5,
        "3-0": 14.0,
    }
    base = engine.predict(
        home_style_tag="均衡",
        away_style_tag="均衡",
        had=had, hhad=hhad, ttg=ttg, crs=crs,
        config={"outcome_enforce_exclude_min_votes": 0, "pool_topk": 5},
    )
    tuned = engine.predict(
        home_style_tag="均衡",
        away_style_tag="均衡",
        had=had, hhad=hhad, ttg=ttg, crs=crs,
        config={"outcome_enforce_exclude_min_votes": 1, "pool_topk": 5},
    )
    base_trace = base["trace"]
    tuned_trace = tuned["trace"]
    assert base_trace["config"]["outcome_enforce_exclude_min_votes"] == 0
    assert tuned_trace["config"]["outcome_enforce_exclude_min_votes"] == 1
    assert len(tuned_trace["stage_b"]["excluded_outcomes"]) >= 1
    assert len(tuned_trace["stage_b"]["excluded_outcomes"]) >= len(base_trace["stage_b"]["excluded_outcomes"])
    assert any(e.get("kind") == "enforce" for e in tuned_trace["stage_b"]["evidence"])
    assert len(tuned_trace["stage_b"]["enforce"]) >= 1


def test_market_flow_v2_enforce_anchor_strategy_prefers_excluding_fake_draw_not_had_pref():
    engine = MarketFlowEngineV2()
    had = {"home": 3.45, "draw": 3.16, "away": 1.91}
    hhad = {"line": 1.0, "home": 1.68, "draw": 3.6, "away": 3.9}
    ttg = {"0": 10.5, "1": 4.5, "2": 3.0, "3": 3.65, "4": 6.5, "5": 14.0}
    crs = {
        "1-0": 10.5,
        "2-0": 17.0,
        "2-1": 11.0,
        "0-0": 10.5,
        "1-1": 6.0,
        "0-1": 7.5,
        "0-2": 9.0,
        "1-2": 7.0,
        "0-3": 19.0,
        "1-3": 16.0,
    }
    tuned5 = engine.predict(
        home_style_tag="双弱",
        away_style_tag="守差攻中",
        had=had, hhad=hhad, ttg=ttg, crs=crs,
        config={
            "outcome_exclude_min_evidence": 3,
            "outcome_enforce_exclude_min_votes": 1,
            "outcome_enforce_strategy": "anchors_v1",
            "pool_topk": 3,
        },
    )
    trace = tuned5["trace"]
    assert trace["config"]["outcome_enforce_strategy"] == "anchors_v1"
    assert len(trace["stage_b"]["excluded_outcomes"]) >= 1
    allowed = set(trace["stage_b"]["allowed_outcomes"])
    assert "away" in allowed
    enforce = trace["stage_b"]["enforce"]
    assert len(enforce) >= 1
    first = enforce[0]
    assert str(first.get("strategy") or "").startswith("anchor")
    assert "anchor_scores" in first
    assert "anchor_white" in first
    assert "anchor_black" in first
    assert first["anchor_scores"].get("away", 9999.0) < first["anchor_scores"].get("draw", -9999.0) + 2.0 or "draw" in trace["stage_b"]["excluded_outcomes"]


def test_market_flow_v2_tuned6_core_draw_high_does_not_label_strong_draw_fake():
    engine = MarketFlowEngineV2()
    had = {"home": 3.45, "draw": 3.16, "away": 1.91}
    hhad = {"line": -1.0, "home": 1.68, "draw": 3.6, "away": 3.9}
    ttg = {"0": 10.5, "1": 4.5, "2": 3.0, "3": 3.65, "4": 6.5, "5": 14.0}
    crs = {
        "1-0": 10.5,
        "2-0": 17.0,
        "2-1": 11.0,
        "0-0": 10.5,
        "1-1": 6.0,
        "0-1": 7.5,
        "0-2": 9.0,
        "1-2": 7.0,
        "0-3": 19.0,
        "1-3": 16.0,
    }
    from app.predictor.models.market_flow import (
        _crs_groups_v2,
        _home_away_goals_signal,
        _total_ballast_signal_v2,
        _crs_pairs_signal,
        _had_hhad_signal,
        _draw_implied_signal_v2,
    )
    by_home, by_away, by_total, home_best, away_best, total_best = _crs_groups_v2(crs)
    pairs = _crs_pairs_signal(crs)
    handicap = _had_hhad_signal(had, hhad)
    draw_impl = _draw_implied_signal_v2(had, crs, ttg=ttg, handicap=handicap, pairs=pairs, total_ballast=None)
    assert draw_impl.get("ratio") and 0.5 <= float(draw_impl["ratio"]) < 0.9
    assert draw_impl.get("tag") == "core_draw_high"
    tuned6 = engine.predict(
        home_style_tag="双弱",
        away_style_tag="守差攻中",
        had=had, hhad=hhad, ttg=ttg, crs=crs,
        config={
            "outcome_exclude_min_evidence": 3,
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
            },
            "pool_topk": 3,
        },
    )
    trace = tuned6["trace"]
    allowed = set(trace["stage_b"]["allowed_outcomes"])
    assert "draw" in allowed
    enforce = trace["stage_b"]["enforce"]
    assert enforce
    first = enforce[0]
    assert first.get("anchor_black", {}).get("draw", 9999.0) <= 0.001 or first.get("anchor_white", {}).get("draw", -1.0) > first.get("anchor_black", {}).get("draw", 9999.0)


def test_market_flow_v2_tuned6_had_pref_never_first_enforce_candidate():
    engine = MarketFlowEngineV2()
    had = {"home": 1.66, "draw": 3.6, "away": 4.02}
    hhad = {"line": 1.0, "home": 3.02, "draw": 3.6, "away": 1.92}
    ttg = {"0": 10.0, "1": 4.5, "2": 3.5, "3": 4.0, "4": 6.8, "5": 13.0}
    crs = {
        "1-0": 8.0,
        "2-0": 9.0,
        "2-1": 7.0,
        "1-1": 7.25,
        "0-0": 11.0,
        "0-1": 9.0,
        "0-2": 12.0,
        "1-2": 11.0,
        "2-2": 13.0,
        "3-0": 14.0,
    }
    tuned6 = engine.predict(
        home_style_tag="攻弱守中",
        away_style_tag="攻守俱佳",
        had=had, hhad=hhad, ttg=ttg, crs=crs,
        config={
            "outcome_exclude_min_evidence": 3,
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
            },
            "pool_topk": 3,
        },
    )
    trace = tuned6["trace"]
    enforce = trace["stage_b"]["enforce"] or []
    assert enforce, "expected at least one enforce entry"
    first_excluded = str(enforce[0]["target_outcome"])
    had_pref = str(trace["stage_b"].get("had_pref") or enforce[0].get("had_pref") or trace["stage_b"]["allowed_outcomes"][0])
    assert first_excluded != had_pref, f"expected first enforce not to exclude had_pref={had_pref}"
    assert "protect_detail" in enforce[0] or "anchor_white" in enforce[0]
