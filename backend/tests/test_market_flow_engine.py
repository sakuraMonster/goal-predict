from app.predictor.models.market_flow import MarketFlowEngine


def test_market_flow_open_game_sample():
    engine = MarketFlowEngine()
    result = engine.predict(
        home_style_tag="大开大合",
        away_style_tag="大开大合",
        had={"home": 1.6, "draw": 3.7, "away": 4.3},
        hhad={"line": -1.0, "home": 2.9, "draw": 3.4, "away": 2.04},
        ttg={"0": 15, "1": 5.85, "2": 4, "3": 3.5, "4": 5, "5": 8.75},
        crs={"2-1": 7, "1-1": 7.5, "1-0": 8.25, "2-0": 9, "3-1": 12, "2-2": 13, "1-2": 13},
    )
    assert result["best_total_goals"] == 3
    assert result["second_total_goals"] == 2
    assert result["best_score"] == "2-1"
    assert isinstance(result["trace"], dict)

    trace = result["trace"]
    for k in ["pool_rule", "pool_scores", "goals_freq", "selected_goals", "selected_scores"]:
        assert k in trace


def test_market_flow_ttg_style_eps_can_swap_best_goals():
    engine = MarketFlowEngine()
    result = engine.predict(
        home_style_tag="均衡",
        away_style_tag="均衡",
        had={"home": 1.66, "draw": 3.6, "away": 4.02},
        hhad={"line": -1.0, "home": 3.02, "draw": 3.6, "away": 1.92},
        ttg={"0": 15, "1": 5.5, "2": 3.5, "3": 3.35, "4": 5.3, "5": 9.8},
        crs={
            "1-0": 8,
            "2-0": 9,
            "2-1": 7,
            "3-0": 15,
            "3-1": 13,
            "0-0": 15,
            "1-1": 7.25,
            "2-2": 13,
            "0-1": 13,
            "0-2": 24,
            "1-2": 12,
            "0-3": 55,
            "1-3": 35,
        },
        ttg_style_eps=0.2,
    )
    assert result["best_total_goals"] == 2
    assert result["second_total_goals"] == 3
    assert result["best_score"] == "1-1"
    assert result["second_score"] == "2-1"

    trace = result["trace"]
    assert trace["ttg_style_eps"] == 0.2
    assert trace["ttg_style_applied"] is True
