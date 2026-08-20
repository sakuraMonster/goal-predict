from datetime import datetime

import pytest
import httpx
from fastapi import FastAPI

from app.api.market_flow import router
from app.db.database import get_db
from app.db.models import JczqPlayOddsSnapshot, MarketFlowPrediction, Match, Team


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


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
        key = next(iter(params.values())) if params else None
        if entity is Match:
            return _FakeResult(self.matches.get(int(key)))
        if entity is Team:
            return _FakeResult(self.teams.get(int(key)))
        if entity is JczqPlayOddsSnapshot:
            return _FakeResult(self.snaps.get(int(key)))
        if entity is MarketFlowPrediction:
            return _FakeResult(self.preds.get(int(key)))
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


@pytest.mark.asyncio
async def test_create_odds_snapshot_ok():
    db = _FakeSession()
    db.matches[1] = Match(id=1, kickoff_time=datetime.utcnow(), home_team_id=10, away_team_id=11)
    app = _build_app(db)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/market-flow/odds-snapshots", json={
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


@pytest.mark.asyncio
async def test_predict_market_flow_ok():
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

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/market-flow/predict/1", json={"odds_snapshot_id": 1})

    assert resp.status_code == 200
    out = resp.json()["data"]
    assert out["best_total_goals"] == 3
    assert out["best_score"] == "2-1"
    assert isinstance(out["trace"], dict)
    assert 1 in db.preds
    assert db.preds[1].odds_snapshot_id == 1


@pytest.mark.asyncio
async def test_predict_market_flow_snapshot_mismatch():
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

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/market-flow/predict/1", json={"odds_snapshot_id": 2})

    assert resp.status_code == 400
    assert resp.json()["error"] == "odds snapshot mismatch"

