from app.db.models import Team, JczqPlayOddsSnapshot, MarketFlowPrediction


def test_market_flow_models_importable():
    assert Team is not None
    assert JczqPlayOddsSnapshot is not None
    assert MarketFlowPrediction is not None


def test_team_style_tag_column_exists():
    assert "style_tag" in Team.__table__.c


def test_market_flow_table_names():
    assert JczqPlayOddsSnapshot.__tablename__ == "jczq_play_odds_snapshots"
    assert MarketFlowPrediction.__tablename__ == "market_flow_predictions"

