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
