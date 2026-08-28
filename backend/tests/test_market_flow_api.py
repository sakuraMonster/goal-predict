from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.market_flow import router
from app.db.database import get_db
from app.db.models import JczqPlayOddsSnapshot, MarketFlowPrediction, Match, Team


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)

    def first(self):
        return self._values[0] if self._values else None


class _FakeResult:
    def __init__(self, value=None, values=None):
        self._value = value
        self._values = values

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        if self._values is not None:
            return _FakeScalars(self._values)
        if self._value is None:
            return _FakeScalars([])
        return _FakeScalars([self._value])


class _FakeSession:
    def __init__(self):
        self.matches: dict[int, Match] = {}
        self.teams: dict[int, Team] = {}
        self.snaps: dict[int, JczqPlayOddsSnapshot] = {}
        self.preds: dict[int, MarketFlowPrediction] = {}
        self._next_snap_id = 1
        self._next_pred_id = 1

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        params = stmt.compile().params
        if entity is Match:
            match_id = next((v for k, v in params.items() if k.startswith("id_")), None)
            if match_id is not None:
                return _FakeResult(self.matches.get(int(match_id)))
            kickoff_keys = [(k, v) for k, v in params.items() if k.startswith("kickoff_time_")]
            kickoff_keys.sort(key=lambda kv: int(kv[0].split("_")[-1]) if kv[0].split("_")[-1].isdigit() else 0)
            start = kickoff_keys[0][1] if len(kickoff_keys) >= 1 else None
            end = kickoff_keys[1][1] if len(kickoff_keys) >= 2 else None
            if start is not None and end is not None:
                values = [
                    m
                    for m in self.matches.values()
                    if m.kickoff_time is not None and start <= m.kickoff_time <= end
                ]
                values.sort(key=lambda m: m.kickoff_time or datetime.min)
                return _FakeResult(values=values)
            return _FakeResult(None)
        if entity is Team:
            team_id = next((v for k, v in params.items() if k.startswith("id_")), None)
            if team_id is None:
                return _FakeResult(None)
            return _FakeResult(self.teams.get(int(team_id)))
        if entity is JczqPlayOddsSnapshot:
            snap_id = next((v for k, v in params.items() if k.startswith("id_")), None)
            if snap_id is not None:
                return _FakeResult(self.snaps.get(int(snap_id)))
            match_id = next((v for k, v in params.items() if k.startswith("match_id_")), None)
            if match_id is not None:
                source = next((v for k, v in params.items() if k.startswith("source_")), None)
                snaps = [s for s in self.snaps.values() if int(s.match_id) == int(match_id)]
                if source is not None:
                    snaps = [s for s in snaps if s.source == source]
                snaps.sort(key=lambda s: s.snapshot_time or datetime.min, reverse=True)
                return _FakeResult(snaps[0] if snaps else None)
            return _FakeResult(None)
        if entity is MarketFlowPrediction:
            match_id = next((v for k, v in params.items() if k.startswith("match_id_")), None)
            if match_id is None:
                return _FakeResult(None)
            return _FakeResult(self.preds.get(int(match_id)))
        return _FakeResult(None)

    def add(self, obj):
        if isinstance(obj, JczqPlayOddsSnapshot):
            if getattr(obj, "id", None) is None:
                obj.id = self._next_snap_id
                self._next_snap_id += 1
            self.snaps[int(obj.id)] = obj
        elif isinstance(obj, MarketFlowPrediction):
            if getattr(obj, "id", None) is None:
                obj.id = self._next_pred_id
                self._next_pred_id += 1
            self.preds[int(obj.match_id)] = obj

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def refresh(self, obj):
        return None


def _build_app(fake_db: _FakeSession) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    async def _override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _override_get_db
    return app


def test_create_odds_snapshot_ok():
    db = _FakeSession()
    db.matches[1] = Match(id=1, kickoff_time=datetime.utcnow(), home_team_id=10, away_team_id=11)
    app = _build_app(db)

    client = TestClient(app)
    resp = client.post("/api/market-flow/odds-snapshots", json={
        "match_id": 1,
        "snapshot_time": "2026-08-20T10:00:00Z",
        "source": "manual_import",
        "had": {"home": 1.9, "draw": 3.2, "away": 3.6},
        "hhad": {"line": -1.0, "home": 3.1, "draw": 3.4, "away": 2.1},
        "ttg": {"2": 3.5, "3": 3.6},
        "crs": {"1-0": 7.5, "2-1": 8.0},
    })

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["odds_snapshot_id"] == 1
    assert 1 in db.snaps
    assert db.snaps[1].match_id == 1


def test_create_odds_snapshot_missing_ttg_or_crs():
    db = _FakeSession()
    db.matches[1] = Match(id=1, kickoff_time=datetime.utcnow(), home_team_id=10, away_team_id=11)
    app = _build_app(db)

    client = TestClient(app)
    resp = client.post("/api/market-flow/odds-snapshots", json={
        "match_id": 1,
        "snapshot_time": "2026-08-20T10:00:00Z",
        "source": "manual_import",
        "had": {"home": 1.9, "draw": 3.2, "away": 3.6},
        "hhad": {"line": -1.0, "home": 3.1, "draw": 3.4, "away": 2.1},
        "ttg": {"2": 3.5, "3": 3.6},
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "crs must be object"

    resp = client.post("/api/market-flow/odds-snapshots", json={
        "match_id": 1,
        "snapshot_time": "2026-08-20T10:00:00Z",
        "source": "manual_import",
        "had": {"home": 1.9, "draw": 3.2, "away": 3.6},
        "hhad": {"line": -1.0, "home": 3.1, "draw": 3.4, "away": 2.1},
        "crs": {"1-0": 7.5, "2-1": 8.0},
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "ttg must be object"

    resp = client.post("/api/market-flow/odds-snapshots", json={
        "match_id": 1,
        "snapshot_time": "2026-08-20T10:00:00Z",
        "source": "manual_import",
        "had": {"home": 1.9, "draw": 3.2, "away": 3.6},
        "hhad": {"line": -1.0, "home": 3.1, "draw": 3.4, "away": 2.1},
        "ttg": {},
        "crs": {"1-0": 7.5},
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "ttg must be non-empty object"

    resp = client.post("/api/market-flow/odds-snapshots", json={
        "match_id": 1,
        "snapshot_time": "2026-08-20T10:00:00Z",
        "source": "manual_import",
        "had": {"home": 1.9, "draw": 3.2, "away": 3.6},
        "hhad": {"line": -1.0, "home": 3.1, "draw": 3.4, "away": 2.1},
        "ttg": {"2": 3.5},
        "crs": {},
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "crs must be non-empty object"


def test_predict_market_flow_ok():
    db = _FakeSession()
    db.matches[1] = Match(id=1, kickoff_time=datetime.utcnow(), home_team_id=10, away_team_id=11)
    db.teams[10] = Team(id=10, name_en="h", style_tag="大开大合")
    db.teams[11] = Team(id=11, name_en="a", style_tag="大开大合")
    db.snaps[1] = JczqPlayOddsSnapshot(
        id=1,
        match_id=1,
        snapshot_time=datetime.utcnow(),
        source="manual_import",
        had_home=1.6,
        had_draw=3.7,
        had_away=4.3,
        hhad_line=-1.0,
        hhad_home=2.9,
        hhad_draw=3.4,
        hhad_away=2.04,
        ttg_odds_json={"0": 15, "1": 5.85, "2": 4, "3": 3.5, "4": 5, "5": 8.75},
        crs_odds_json={"2-1": 7, "1-1": 7.5, "1-0": 8.25, "2-0": 9, "3-1": 12, "2-2": 13, "1-2": 13},
    )
    app = _build_app(db)

    client = TestClient(app)
    resp = client.post("/api/market-flow/predict/1", json={"odds_snapshot_id": 1})

    assert resp.status_code == 200
    out = resp.json()["data"]
    assert out["best_total_goals"] == 3
    assert out["best_score"] == "2-1"
    assert isinstance(out["trace"], dict)
    assert 1 in db.preds
    assert db.preds[1].odds_snapshot_id == 1


def test_predict_market_flow_v2_persists_model_version_and_trace_config():
    db = _FakeSession()
    db.matches[1] = Match(id=1, kickoff_time=datetime.utcnow(), home_team_id=10, away_team_id=11)
    db.teams[10] = Team(id=10, name_en="h", style_tag="双弱")
    db.teams[11] = Team(id=11, name_en="a", style_tag="守差攻中")
    db.snaps[1] = JczqPlayOddsSnapshot(
        id=1,
        match_id=1,
        snapshot_time=datetime.utcnow(),
        source="manual_import",
        had_home=3.45,
        had_draw=3.16,
        had_away=1.91,
        hhad_line=1.0,
        hhad_home=1.68,
        hhad_draw=3.6,
        hhad_away=3.9,
        ttg_odds_json={"0": 10.5, "1": 4.5, "2": 3, "3": 3.65, "4": 6.5, "5": 14},
        crs_odds_json={
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
    )
    app = _build_app(db)

    client = TestClient(app)
    resp = client.post("/api/market-flow/predict/1", json={
        "odds_snapshot_id": 1,
        "engine_version": "v2",
        "config": {"pool_topk": 6},
    })

    assert resp.status_code == 200
    out = resp.json()["data"]
    assert out["trace"]["version"] == "v2"
    assert out["trace"]["config"]["pool_topk"] == 6
    assert db.preds[1].model_version == "marketflow_v2"
    assert isinstance(db.preds[1].trace_json, dict)
    assert "config" in db.preds[1].trace_json


def test_predict_market_flow_snapshot_mismatch():
    db = _FakeSession()
    db.matches[1] = Match(id=1, kickoff_time=datetime.utcnow(), home_team_id=10, away_team_id=11)
    db.snaps[2] = JczqPlayOddsSnapshot(
        id=2,
        match_id=999,
        snapshot_time=datetime.utcnow(),
        source="manual_import",
        had_home=1.9,
        had_draw=3.2,
        had_away=3.6,
        hhad_line=-1.0,
        hhad_home=3.1,
        hhad_draw=3.4,
        hhad_away=2.1,
        ttg_odds_json={},
        crs_odds_json={},
    )
    app = _build_app(db)

    client = TestClient(app)
    resp = client.post("/api/market-flow/predict/1", json={"odds_snapshot_id": 2})

    assert resp.status_code == 400
    assert resp.json()["error"] == "odds snapshot mismatch"


def test_predict_market_flow_incomplete_engine_result_returns_400():
    db = _FakeSession()
    db.matches[1] = Match(id=1, kickoff_time=datetime.utcnow(), home_team_id=10, away_team_id=11)
    db.snaps[1] = JczqPlayOddsSnapshot(
        id=1,
        match_id=1,
        snapshot_time=datetime.utcnow(),
        source="manual_import",
        had_home=1.6,
        had_draw=3.7,
        had_away=4.3,
        hhad_line=-1.0,
        hhad_home=2.9,
        hhad_draw=3.4,
        hhad_away=2.04,
        ttg_odds_json={"0": 15, "1": 5.85},
        crs_odds_json={"1-0": 7.5},
    )
    app = _build_app(db)

    client = TestClient(app)
    resp = client.post("/api/market-flow/predict/1", json={"odds_snapshot_id": 1})
    assert resp.status_code == 400
    assert "marketflow engine result missing fields:" in resp.json()["error"]
    assert 1 not in db.preds


def test_backtest_market_flow_ok_and_stats():
    db = _FakeSession()
    db.matches[1] = Match(
        id=1,
        kickoff_time=datetime(2026, 8, 20, 10, 0, 0),
        home_team_id=10,
        away_team_id=11,
        home_score=2,
        away_score=1,
    )
    db.matches[2] = Match(
        id=2,
        kickoff_time=datetime(2026, 8, 20, 11, 0, 0),
        home_team_id=10,
        away_team_id=11,
        home_score=9,
        away_score=0,
    )
    db.teams[10] = Team(id=10, name_en="h", style_tag="大开大合")
    db.teams[11] = Team(id=11, name_en="a", style_tag="大开大合")
    db.snaps[1] = JczqPlayOddsSnapshot(
        id=1,
        match_id=1,
        snapshot_time=datetime(2026, 8, 20, 9, 0, 0),
        source="manual_import",
        had_home=1.6,
        had_draw=3.7,
        had_away=4.3,
        hhad_line=-1.0,
        hhad_home=2.9,
        hhad_draw=3.4,
        hhad_away=2.04,
        ttg_odds_json={"0": 15, "1": 5.85, "2": 4, "3": 3.5, "4": 5, "5": 8.75},
        crs_odds_json={"2-1": 7, "1-1": 7.5, "1-0": 8.25, "2-0": 9, "3-1": 12, "2-2": 13, "1-2": 13},
    )
    db.snaps[2] = JczqPlayOddsSnapshot(
        id=2,
        match_id=1,
        snapshot_time=datetime(2026, 8, 20, 9, 30, 0),
        source="manual_import",
        had_home=1.6,
        had_draw=3.7,
        had_away=4.3,
        hhad_line=-1.0,
        hhad_home=2.9,
        hhad_draw=3.4,
        hhad_away=2.04,
        ttg_odds_json={"0": 15, "1": 5.85, "2": 4, "3": 3.5, "4": 5, "5": 8.75},
        crs_odds_json={"2-1": 7, "1-1": 7.5, "1-0": 8.25, "2-0": 9, "3-1": 12, "2-2": 13, "1-2": 13},
    )
    db.snaps[3] = JczqPlayOddsSnapshot(
        id=3,
        match_id=2,
        snapshot_time=datetime(2026, 8, 20, 9, 40, 0),
        source="manual_import",
        had_home=1.6,
        had_draw=3.7,
        had_away=4.3,
        hhad_line=-1.0,
        hhad_home=2.9,
        hhad_draw=3.4,
        hhad_away=2.04,
        ttg_odds_json={"0": 15, "1": 5.85, "2": 4, "3": 3.5, "4": 5, "5": 8.75},
        crs_odds_json={"2-1": 7, "1-1": 7.5, "1-0": 8.25, "2-0": 9, "3-1": 12, "2-2": 13, "1-2": 13},
    )
    app = _build_app(db)

    client = TestClient(app)
    resp = client.post("/api/market-flow/backtest", json={
        "start_kickoff": "2026-08-20T00:00:00Z",
        "end_kickoff": "2026-08-21T00:00:00Z",
        "source": "manual_import",
        "overwrite": True,
    })

    assert resp.status_code == 200
    out = resp.json()["data"]
    assert out["total"] == 2
    assert out["predicted"] == 2
    assert out["skipped"] == 0
    assert out["goals_hit_best"] == 1
    assert out["goals_hit_top2"] == 1
    assert out["score_hit_best"] == 1
    assert out["score_hit_top2"] == 1
    assert db.preds[1].odds_snapshot_id == 2


def test_backtest_market_flow_skip_existing_and_still_score():
    db = _FakeSession()
    db.matches[1] = Match(
        id=1,
        kickoff_time=datetime(2026, 8, 20, 10, 0, 0),
        home_team_id=10,
        away_team_id=11,
        home_score=2,
        away_score=1,
    )
    db.matches[2] = Match(
        id=2,
        kickoff_time=datetime(2026, 8, 20, 11, 0, 0),
        home_team_id=10,
        away_team_id=11,
        home_score=9,
        away_score=0,
    )
    db.teams[10] = Team(id=10, name_en="h", style_tag="大开大合")
    db.teams[11] = Team(id=11, name_en="a", style_tag="大开大合")
    db.snaps[1] = JczqPlayOddsSnapshot(
        id=1,
        match_id=1,
        snapshot_time=datetime(2026, 8, 20, 9, 30, 0),
        source="manual_import",
        had_home=1.6,
        had_draw=3.7,
        had_away=4.3,
        hhad_line=-1.0,
        hhad_home=2.9,
        hhad_draw=3.4,
        hhad_away=2.04,
        ttg_odds_json={"0": 15, "1": 5.85, "2": 4, "3": 3.5, "4": 5, "5": 8.75},
        crs_odds_json={"2-1": 7, "1-1": 7.5, "1-0": 8.25, "2-0": 9, "3-1": 12, "2-2": 13, "1-2": 13},
    )
    db.snaps[2] = JczqPlayOddsSnapshot(
        id=2,
        match_id=2,
        snapshot_time=datetime(2026, 8, 20, 9, 40, 0),
        source="manual_import",
        had_home=1.6,
        had_draw=3.7,
        had_away=4.3,
        hhad_line=-1.0,
        hhad_home=2.9,
        hhad_draw=3.4,
        hhad_away=2.04,
        ttg_odds_json={"0": 15, "1": 5.85, "2": 4, "3": 3.5, "4": 5, "5": 8.75},
        crs_odds_json={"2-1": 7, "1-1": 7.5, "1-0": 8.25, "2-0": 9, "3-1": 12, "2-2": 13, "1-2": 13},
    )
    db.preds[1] = MarketFlowPrediction(
        id=1,
        match_id=1,
        odds_snapshot_id=1,
        model_version="marketflow_v1",
        created_at=datetime.utcnow(),
        home_style_tag="均衡",
        away_style_tag="均衡",
        best_total_goals=3,
        second_total_goals=2,
        best_score="2-1",
        second_score="1-1",
        trace_json={},
    )
    app = _build_app(db)

    client = TestClient(app)
    resp = client.post("/api/market-flow/backtest", json={
        "start_kickoff": "2026-08-20T00:00:00Z",
        "end_kickoff": "2026-08-21T00:00:00Z",
        "source": "manual_import",
        "overwrite": False,
    })

    assert resp.status_code == 200
    out = resp.json()["data"]
    assert out["total"] == 2
    assert out["predicted"] == 1
    assert out["skipped"] == 1
    assert out["goals_hit_best"] == 1
    assert out["goals_hit_top2"] == 1
    assert out["score_hit_best"] == 1
    assert out["score_hit_top2"] == 1


def test_backtest_market_flow_skip_incomplete_engine_result():
    db = _FakeSession()
    db.matches[1] = Match(
        id=1,
        kickoff_time=datetime(2026, 8, 20, 10, 0, 0),
        home_team_id=10,
        away_team_id=11,
        home_score=2,
        away_score=1,
    )
    db.matches[2] = Match(
        id=2,
        kickoff_time=datetime(2026, 8, 20, 11, 0, 0),
        home_team_id=10,
        away_team_id=11,
        home_score=1,
        away_score=0,
    )
    db.teams[10] = Team(id=10, name_en="h", style_tag="大开大合")
    db.teams[11] = Team(id=11, name_en="a", style_tag="大开大合")
    db.snaps[1] = JczqPlayOddsSnapshot(
        id=1,
        match_id=1,
        snapshot_time=datetime(2026, 8, 20, 9, 30, 0),
        source="manual_import",
        had_home=1.6,
        had_draw=3.7,
        had_away=4.3,
        hhad_line=-1.0,
        hhad_home=2.9,
        hhad_draw=3.4,
        hhad_away=2.04,
        ttg_odds_json={"0": 15, "1": 5.85, "2": 4, "3": 3.5, "4": 5, "5": 8.75},
        crs_odds_json={"2-1": 7, "1-1": 7.5, "1-0": 8.25, "2-0": 9, "3-1": 12, "2-2": 13, "1-2": 13},
    )
    db.snaps[2] = JczqPlayOddsSnapshot(
        id=2,
        match_id=2,
        snapshot_time=datetime(2026, 8, 20, 9, 40, 0),
        source="manual_import",
        had_home=1.6,
        had_draw=3.7,
        had_away=4.3,
        hhad_line=-1.0,
        hhad_home=2.9,
        hhad_draw=3.4,
        hhad_away=2.04,
        ttg_odds_json={"0": 15, "1": 5.85},
        crs_odds_json={"1-0": 7.5},
    )
    app = _build_app(db)

    client = TestClient(app)
    resp = client.post("/api/market-flow/backtest", json={
        "start_kickoff": "2026-08-20T00:00:00Z",
        "end_kickoff": "2026-08-21T00:00:00Z",
        "source": "manual_import",
        "overwrite": True,
    })

    assert resp.status_code == 200
    out = resp.json()["data"]
    assert out["total"] == 2
    assert out["predicted"] == 1
    assert out["skipped"] == 1
    assert any(x["match_id"] == 2 and x["reason"] == "engine_result_incomplete" for x in out["skipped_detail"])
    assert 1 in db.preds
    assert 2 not in db.preds
