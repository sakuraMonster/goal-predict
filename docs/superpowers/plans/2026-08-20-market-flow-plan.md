# MarketFlow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一条独立的 MarketFlow 预测链路，基于“盘口结构 + 风格标签”输出进球数/比分的最优与次优解，并支持赔率快照导入与批量回测（不改前端）。

**Architecture:** 新增独立数据表 `jczq_play_odds_snapshots` 与 `market_flow_predictions` 存储赔率快照和预测结果；新增纯函数推理引擎 `MarketFlowEngine`；新增独立 FastAPI 路由 `/api/market-flow/*` 负责导入、预测与回测。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy asyncio, PostgreSQL, pytest, pytest-asyncio

## Global Constraints

- 不改前端任何页面与交互
- 不实现赔率自动采集/同步（仅提供导入接口）
- 风格标签来自数据库配置（Team.style_tag）
- 不做去水/隐含概率归一化（仅按赔率结构做推断）
- 输出固定为：进球数最优/次优各 1 个；比分最优/次优各 1 个；并保留 trace_json 可复盘
- 数据结构与现有预测体系强隔离（独立表 + 独立 API）

---

## File Map

**Modify**
- `e:/zhangxuejun/new-thinking/ricking-03/backend/app/db/models.py`（Team.style_tag；新增两张表的 ORM）
- `e:/zhangxuejun/new-thinking/ricking-03/backend/main.py`（注册新路由）

**Create**
- `e:/zhangxuejun/new-thinking/ricking-03/backend/app/predictor/models/market_flow.py`（推理引擎与纯函数工具）
- `e:/zhangxuejun/new-thinking/ricking-03/backend/app/api/market_flow.py`（导入/预测/回测 API）
- `e:/zhangxuejun/new-thinking/ricking-03/backend/tools/migrate_market_flow.py`（一次性迁移脚本：建表/加字段）
- `e:/zhangxuejun/new-thinking/ricking-03/backend/tests/test_market_flow_engine.py`（引擎单测）
- `e:/zhangxuejun/new-thinking/ricking-03/backend/tests/test_market_flow_api.py`（API 单测，使用 TestClient + 事务隔离）

---

### Task 1: 数据模型与迁移脚本（独立表 + 风格标签）

**Files:**
- Modify: [models.py](file:///e:/zhangxuejun/new-thinking/ricking-03/backend/app/db/models.py)
- Create: `e:/zhangxuejun/new-thinking/ricking-03/backend/tools/migrate_market_flow.py`

**Interfaces:**
- Produces: SQLAlchemy models `JczqPlayOddsSnapshot`, `MarketFlowPrediction`；Team 字段 `style_tag`
- Produces: 可重复执行的迁移脚本（ALTER/CREATE IF NOT EXISTS）

- [ ] **Step 1: 写迁移脚本（先不改 ORM）**

```python
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, ".")

from sqlalchemy import text
from app.db.database import async_session


async def main():
    async with async_session() as db:
        await db.execute(text("ALTER TABLE teams ADD COLUMN IF NOT EXISTS style_tag VARCHAR(20)"))

        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS jczq_play_odds_snapshots (
                id SERIAL PRIMARY KEY,
                match_id INTEGER NOT NULL REFERENCES matches(id),
                snapshot_time TIMESTAMP NOT NULL,
                source VARCHAR(50) NOT NULL,

                had_home DOUBLE PRECISION,
                had_draw DOUBLE PRECISION,
                had_away DOUBLE PRECISION,

                hhad_line DOUBLE PRECISION,
                hhad_home DOUBLE PRECISION,
                hhad_draw DOUBLE PRECISION,
                hhad_away DOUBLE PRECISION,

                ttg_odds_json JSONB,
                crs_odds_json JSONB
            )
        """))

        await db.execute(text("CREATE INDEX IF NOT EXISTS idx_jczq_odds_match_time ON jczq_play_odds_snapshots(match_id, snapshot_time DESC)"))

        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS market_flow_predictions (
                id SERIAL PRIMARY KEY,
                match_id INTEGER NOT NULL UNIQUE REFERENCES matches(id),
                odds_snapshot_id INTEGER NOT NULL REFERENCES jczq_play_odds_snapshots(id),
                model_version VARCHAR(50) NOT NULL,
                created_at TIMESTAMP NOT NULL,

                home_style_tag VARCHAR(20) NOT NULL,
                away_style_tag VARCHAR(20) NOT NULL,

                best_total_goals INTEGER,
                second_total_goals INTEGER,
                best_score VARCHAR(10),
                second_score VARCHAR(10),

                trace_json JSONB
            )
        """))

        await db.execute(text("CREATE INDEX IF NOT EXISTS idx_market_flow_pred_created_at ON market_flow_predictions(created_at DESC)"))

        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 运行迁移脚本验证无报错**

Run:

```powershell
python .\tools\migrate_market_flow.py
```

Expected:
- 退出码 0
- 数据库新增两张表、teams 新增 style_tag 字段

- [ ] **Step 3: 修改 ORM（models.py）加入新字段与新模型**

在 `Team` 类中新增字段：

```python
style_tag = Column(String(20))
```

新增两个 ORM 类（建议放在 OddsSnapshot 后或文件末尾），示例：

```python
class JczqPlayOddsSnapshot(Base):
    __tablename__ = "jczq_play_odds_snapshots"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    snapshot_time = Column(DateTime, nullable=False)
    source = Column(String(50), nullable=False)

    had_home = Column(Float)
    had_draw = Column(Float)
    had_away = Column(Float)

    hhad_line = Column(Float)
    hhad_home = Column(Float)
    hhad_draw = Column(Float)
    hhad_away = Column(Float)

    ttg_odds_json = Column(JSON)
    crs_odds_json = Column(JSON)


class MarketFlowPrediction(Base):
    __tablename__ = "market_flow_predictions"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, unique=True, index=True)
    odds_snapshot_id = Column(Integer, ForeignKey("jczq_play_odds_snapshots.id"), nullable=False)
    model_version = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    home_style_tag = Column(String(20), nullable=False)
    away_style_tag = Column(String(20), nullable=False)

    best_total_goals = Column(Integer)
    second_total_goals = Column(Integer)
    best_score = Column(String(10))
    second_score = Column(String(10))

    trace_json = Column(JSON)
```

- [ ] **Step 4: 用 pytest 只做导入检查（先不写业务测试）**

Run:

```powershell
python -c "from app.db.models import Team, JczqPlayOddsSnapshot, MarketFlowPrediction; print('ok')"
```

Expected:
- 输出 `ok`

- [ ] **Step 5: Commit**

```powershell
git add backend/app/db/models.py backend/tools/migrate_market_flow.py
git commit -m "feat: add marketflow db models and migration script"
```

---

### Task 2: MarketFlow 推理引擎（纯函数 + trace）

**Files:**
- Create: `e:/zhangxuejun/new-thinking/ricking-03/backend/app/predictor/models/market_flow.py`
- Test: `e:/zhangxuejun/new-thinking/ricking-03/backend/tests/test_market_flow_engine.py`

**Interfaces:**
- Produces: `MarketFlowEngine.predict(...) -> dict`
- Produces: trace_json 结构稳定（便于回测与复盘）

- [ ] **Step 1: 写引擎测试（红）**

```python
import pytest
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
    assert result["second_score"] in {"2-0", "1-1"}
    assert isinstance(result["trace"], dict)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
pytest -q backend/tests/test_market_flow_engine.py::test_market_flow_open_game_sample
```

Expected:
- FAIL（market_flow.py 未实现或类不存在）

- [ ] **Step 3: 实现 MarketFlowEngine（最小实现让测试过）**

```python
from __future__ import annotations

from dataclasses import dataclass
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


def _score_outcome(h: int, a: int) -> str:
    if h > a:
        return "home"
    if h == a:
        return "draw"
    return "away"


def _hhad_outcome(h: int, a: int, line: float | None) -> str:
    adj = float(h) + float(line or 0.0)
    if adj > float(a):
        return "home"
    if adj == float(a):
        return "draw"
    return "away"


def _detect_gap(sorted_odds: list[float], gap_abs: float = 2.5, gap_ratio: float = 1.2) -> int | None:
    for i in range(len(sorted_odds) - 1):
        cur = sorted_odds[i]
        nxt = sorted_odds[i + 1]
        if cur <= 0:
            continue
        if (nxt - cur) >= gap_abs and (nxt / cur) >= gap_ratio:
            return i
    return None


def _matchup_style(home_style: str | None, away_style: str | None) -> str:
    hs = home_style or "均衡"
    aw = away_style or "均衡"
    if "防守" in hs or "防守" in aw:
        if ("大开大合" in hs) and ("大开大合" in aw):
            return "均衡"
        return "防守型"
    if "大开大合" in hs or "大开大合" in aw:
        return "大开大合"
    return "均衡"


def _style_priority(style: str) -> list[int]:
    if style == "防守型":
        return [1, 2, 3, 4, 5, 0]
    if style == "大开大合":
        return [3, 4, 2, 5, 1, 0]
    return [2, 3, 1, 4, 0, 5]


@dataclass(frozen=True)
class MarketFlowResult:
    best_total_goals: int | None
    second_total_goals: int | None
    best_score: str | None
    second_score: str | None
    trace: dict[str, Any]


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
    ) -> dict[str, Any]:
        had = had or {}
        hhad = hhad or {}
        ttg = ttg or {}
        crs = crs or {}

        crs_items = [(k, float(v)) for k, v in crs.items() if isinstance(v, (int, float)) and float(v) > 0]
        crs_items.sort(key=lambda x: x[1])

        odds_sorted = [o for _, o in crs_items]
        gap_idx = _detect_gap(odds_sorted) if odds_sorted else None
        if gap_idx is None:
            pool = crs_items[:topk]
            pool_rule = {"type": "topk", "k": topk}
        else:
            pool = crs_items[: gap_idx + 1]
            pool_rule = {"type": "gap", "gap_idx": gap_idx}
        if len(pool) < min_pool:
            pool = crs_items[:min_pool]

        pool_scores = [k for k, _ in pool]
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

        def _ttg_get(g: int) -> float | None:
            if str(g) in ttg and isinstance(ttg[str(g)], (int, float)):
                return float(ttg[str(g)])
            if g in ttg and isinstance(ttg[g], (int, float)):
                return float(ttg[g])
            return None

        def _goal_sort_key(g: int):
            odd = _ttg_get(g)
            return (odd if odd is not None else 1e9, -goals_freq.get(g, 0), priorities.index(g) if g in priorities else 999)

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

        best_score = _best_score_for_goals(best_g) if best_g is not None else None
        second_a = None
        if best_g is not None:
            same_g_scores = [(s, o) for s, o in crs_items if _parse_score(s) and sum(_parse_score(s)) == best_g]
            if same_g_scores:
                same_g_scores.sort(key=lambda x: x[1])
                if len(same_g_scores) >= 2:
                    second_a = same_g_scores[1][0]
        second_b = _best_score_for_goals(second_g) if second_g is not None else None

        def _had_pref() -> str | None:
            h = had.get("home")
            d = had.get("draw")
            a = had.get("away")
            if not all(isinstance(x, (int, float)) for x in [h, d, a]):
                return None
            vals = {"home": float(h), "draw": float(d), "away": float(a)}
            return min(vals.items(), key=lambda x: x[1])[0]

        def _hhad_odd_for_outcome(outcome: str) -> float | None:
            key = {"home": "home", "draw": "draw", "away": "away"}[outcome]
            v = hhad.get(key)
            if isinstance(v, (int, float)) and float(v) > 0:
                return float(v)
            return None

        def _pick_second(s1: str | None, s2: str | None) -> str | None:
            if s1 and not s2:
                return s1
            if s2 and not s1:
                return s2
            if not s1 and not s2:
                return None
            if s1 == s2:
                return s1

            pref = _had_pref()
            if pref:
                p1 = _parse_score(s1)
                p2 = _parse_score(s2)
                if p1 and p2:
                    o1 = _score_outcome(p1[0], p1[1])
                    o2 = _score_outcome(p2[0], p2[1])
                    if o1 == pref and o2 != pref:
                        return s1
                    if o2 == pref and o1 != pref:
                        return s2

            line = hhad.get("line")
            p1 = _parse_score(s1)
            p2 = _parse_score(s2)
            if p1 and p2:
                r1 = _hhad_outcome(p1[0], p1[1], float(line) if isinstance(line, (int, float)) else None)
                r2 = _hhad_outcome(p2[0], p2[1], float(line) if isinstance(line, (int, float)) else None)
                odd1 = _hhad_odd_for_outcome(r1)
                odd2 = _hhad_odd_for_outcome(r2)
                if odd1 is not None and odd2 is not None and odd1 != odd2:
                    return s1 if odd1 < odd2 else s2

            o_s1 = crs.get(s1)
            o_s2 = crs.get(s2)
            if isinstance(o_s1, (int, float)) and isinstance(o_s2, (int, float)) and float(o_s1) != float(o_s2):
                return s1 if float(o_s1) < float(o_s2) else s2

            return s1

        second_score = _pick_second(second_a, second_b)

        trace = {
            "input": {
                "home_style_tag": home_style_tag,
                "away_style_tag": away_style_tag,
            },
            "matchup_style": matchup_style,
            "pool_rule": pool_rule,
            "pool_scores": pool,
            "goals_freq": goals_freq,
            "goal_priority": priorities,
            "selected_goals": {"best": best_g, "second": second_g},
            "selected_scores": {"best": best_score, "second": second_score},
        }

        return MarketFlowResult(
            best_total_goals=best_g,
            second_total_goals=second_g,
            best_score=best_score,
            second_score=second_score,
            trace=trace,
        ).__dict__
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
pytest -q backend/tests/test_market_flow_engine.py::test_market_flow_open_game_sample
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```powershell
git add backend/app/predictor/models/market_flow.py backend/tests/test_market_flow_engine.py
git commit -m "feat: add marketflow engine with trace"
```

---

### Task 3: MarketFlow API（导入赔率快照 + 单场预测）

**Files:**
- Create: `e:/zhangxuejun/new-thinking/ricking-03/backend/app/api/market_flow.py`
- Modify: [main.py](file:///e:/zhangxuejun/new-thinking/ricking-03/backend/main.py)
- Test: `e:/zhangxuejun/new-thinking/ricking-03/backend/tests/test_market_flow_api.py`

**Interfaces:**
- Produces: `POST /api/market-flow/odds-snapshots`
- Produces: `POST /api/market-flow/predict/{match_id}`

- [ ] **Step 1: 写 API 测试（红）**

```python
import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_market_flow_import_and_predict():
    from main import app
    client = TestClient(app)

    payload = {
        "match_id": 1,
        "snapshot_time": "2026-08-20T12:00:00",
        "source": "manual_import",
        "had": {"home": 1.6, "draw": 3.7, "away": 4.3},
        "hhad": {"line": -1, "home": 2.9, "draw": 3.4, "away": 2.04},
        "ttg": {"0": 15, "1": 5.85, "2": 4, "3": 3.5, "4": 5, "5": 8.75},
        "crs": {"2-1": 7, "1-1": 7.5, "1-0": 8.25, "2-0": 9},
    }

    r1 = client.post("/api/market-flow/odds-snapshots", json=payload)
    assert r1.status_code == 200
    odds_snapshot_id = r1.json()["data"]["odds_snapshot_id"]

    r2 = client.post(f"/api/market-flow/predict/{payload['match_id']}", json={"odds_snapshot_id": odds_snapshot_id})
    assert r2.status_code == 200
    data = r2.json()["data"]
    assert data["best_total_goals"] in [2, 3, 4, 1, 0, 5]
    assert "trace" in data
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
pytest -q backend/tests/test_market_flow_api.py::test_market_flow_import_and_predict
```

Expected:
- FAIL（路由未注册/接口不存在）

- [ ] **Step 3: 新增 API 路由文件**

`backend/app/api/market_flow.py` 初始内容（参考现有 API 风格，使用 dict 入参并手工校验）：

```python
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.database import get_db
from app.db.models import Match, Team, JczqPlayOddsSnapshot, MarketFlowPrediction
from app.predictor.models.market_flow import MarketFlowEngine


router = APIRouter(prefix="/api/market-flow", tags=["market-flow"])


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


@router.post("/odds-snapshots")
async def create_odds_snapshot(payload: dict, db: AsyncSession = Depends(get_db)):
    match_id = payload.get("match_id")
    snapshot_time = _parse_dt(payload.get("snapshot_time", ""))
    source = payload.get("source")

    if not isinstance(match_id, int) or match_id <= 0:
        return {"error": "invalid match_id"}, 400
    if snapshot_time is None:
        return {"error": "invalid snapshot_time"}, 400
    if not isinstance(source, str) or not source:
        return {"error": "invalid source"}, 400

    had = payload.get("had") or {}
    hhad = payload.get("hhad") or {}
    ttg = payload.get("ttg") or {}
    crs = payload.get("crs") or {}

    match_row = await db.execute(select(Match).where(Match.id == match_id))
    match = match_row.scalar_one_or_none()
    if not match:
        return {"error": "match not found"}, 404

    row = JczqPlayOddsSnapshot(
        match_id=match_id,
        snapshot_time=snapshot_time,
        source=source,
        had_home=had.get("home"),
        had_draw=had.get("draw"),
        had_away=had.get("away"),
        hhad_line=hhad.get("line"),
        hhad_home=hhad.get("home"),
        hhad_draw=hhad.get("draw"),
        hhad_away=hhad.get("away"),
        ttg_odds_json=ttg,
        crs_odds_json=crs,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return {"data": {"odds_snapshot_id": row.id}}


@router.post("/predict/{match_id}")
async def predict_market_flow(match_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    odds_snapshot_id = payload.get("odds_snapshot_id")
    if not isinstance(odds_snapshot_id, int) or odds_snapshot_id <= 0:
        return {"error": "invalid odds_snapshot_id"}, 400

    odds_row = await db.execute(select(JczqPlayOddsSnapshot).where(
        JczqPlayOddsSnapshot.id == odds_snapshot_id,
        JczqPlayOddsSnapshot.match_id == match_id,
    ))
    odds = odds_row.scalar_one_or_none()
    if not odds:
        return {"error": "odds snapshot not found"}, 404

    match_row = await db.execute(select(Match).where(Match.id == match_id))
    match = match_row.scalar_one_or_none()
    if not match:
        return {"error": "match not found"}, 404

    home = await db.get(Team, match.home_team_id) if match.home_team_id else None
    away = await db.get(Team, match.away_team_id) if match.away_team_id else None

    engine = MarketFlowEngine()
    result = engine.predict(
        home_style_tag=(home.style_tag if home else None),
        away_style_tag=(away.style_tag if away else None),
        had={"home": odds.had_home, "draw": odds.had_draw, "away": odds.had_away},
        hhad={"line": odds.hhad_line, "home": odds.hhad_home, "draw": odds.hhad_draw, "away": odds.hhad_away},
        ttg=(odds.ttg_odds_json or {}),
        crs=(odds.crs_odds_json or {}),
    )

    model_version = payload.get("model_version") or "marketflow_v1"
    pred = MarketFlowPrediction(
        match_id=match_id,
        odds_snapshot_id=odds.id,
        model_version=model_version,
        home_style_tag=(home.style_tag if home and home.style_tag else "均衡"),
        away_style_tag=(away.style_tag if away and away.style_tag else "均衡"),
        best_total_goals=result.get("best_total_goals"),
        second_total_goals=result.get("second_total_goals"),
        best_score=result.get("best_score"),
        second_score=result.get("second_score"),
        trace_json=result.get("trace"),
    )

    existing_row = await db.execute(select(MarketFlowPrediction).where(MarketFlowPrediction.match_id == match_id))
    existing = existing_row.scalar_one_or_none()
    if existing:
        for k, v in pred.__dict__.items():
            if k.startswith("_"):
                continue
            if k in ["id"]:
                continue
            setattr(existing, k, v)
        await db.commit()
        await db.refresh(existing)
        saved = existing
    else:
        db.add(pred)
        await db.commit()
        await db.refresh(pred)
        saved = pred

    return {"data": {
        "best_total_goals": saved.best_total_goals,
        "second_total_goals": saved.second_total_goals,
        "best_score": saved.best_score,
        "second_score": saved.second_score,
        "trace": saved.trace_json,
    }}
```

- [ ] **Step 4: 注册路由到 main.py**

将 main.py 的 import 和 include_router 扩展为：

```python
from app.api import matches, predictions, reports, admin, mappings, teams, market_flow
...
app.include_router(market_flow.router)
```

- [ ] **Step 5: 运行 API 测试（预期仍可能因 DB 依赖失败）**

Run:

```powershell
pytest -q backend/tests/test_market_flow_api.py::test_market_flow_import_and_predict
```

Expected:
- 若测试环境无数据库：FAIL（需要在 Task 4 做测试隔离策略）
- 若已有本地数据库并存在 match_id=1：PASS

- [ ] **Step 6: Commit**

```powershell
git add backend/app/api/market_flow.py backend/main.py
git commit -m "feat: add marketflow api endpoints"
```

---

### Task 4: 回测端点（批量跑 + 命中统计）

**Files:**
- Modify: `e:/zhangxuejun/new-thinking/ricking-03/backend/app/api/market_flow.py`
- Test: `e:/zhangxuejun/new-thinking/ricking-03/backend/tests/test_market_flow_engine.py`（或新增 test）

**Interfaces:**
- Produces: `POST /api/market-flow/backtest`

- [ ] **Step 1: 定义 backtest 返回结构（先写测试：纯函数统计）**

```python
def test_market_flow_backtest_stats_shape():
    stats = {
        "total": 10,
        "goals_hit_best": 3,
        "goals_hit_top2": 5,
        "score_hit_best": 1,
        "score_hit_top2": 2,
    }
    assert set(stats.keys()) == {"total", "goals_hit_best", "goals_hit_top2", "score_hit_best", "score_hit_top2"}
```

- [ ] **Step 2: 在 API 实现 /backtest**

建议输入：
- `start_kickoff` / `end_kickoff`（ISO 字符串）
- `source`（可选）
- `overwrite`（可选，默认 false）

实现要点（在 market_flow.py 内）：
- 查询 matches（kickoff_time 范围）并 join 最近一条 odds snapshot（或按 source 过滤）
- 对每场调用 MarketFlowEngine.predict 并 upsert market_flow_predictions
- 有实际比分（match.home_score/away_score 非空）时计算命中：
  - goals_hit_best: actual_total_goals == best_total_goals
  - goals_hit_top2: actual_total_goals in {best_total_goals, second_total_goals}
  - score_hit_best: actual_score == best_score
  - score_hit_top2: actual_score in {best_score, second_score}

- [ ] **Step 3: 手工验证（本地跑一段时间范围）**

Run:

```powershell
curl -X POST http://localhost:8008/api/market-flow/backtest -H "Content-Type: application/json" -d "{\"start_kickoff\":\"2026-08-01T00:00:00\",\"end_kickoff\":\"2026-08-20T00:00:00\"}"
```

Expected:
- 返回包含 total 与命中统计字段

- [ ] **Step 4: Commit**

```powershell
git add backend/app/api/market_flow.py
git commit -m "feat: add marketflow backtest endpoint"
```

---

### Task 5: 测试隔离与最小可跑回测数据注入

**Files:**
- Modify: `e:/zhangxuejun/new-thinking/ricking-03/backend/tests/test_market_flow_api.py`
- (可选) Create: `e:/zhangxuejun/new-thinking/ricking-03/backend/tests/conftest.py`

**Interfaces:**
- Produces: API 测试在无真实数据库数据时也可跑（使用事务回滚 + 测试数据插入）

- [ ] **Step 1: 建立测试数据库连接约定**

要求：
- 测试运行前设置 `DATABASE_URL` 指向一个可写入的本地测试库（或同库单独 schema）
- 测试用例中插入 Match/Team/OddsSnapshot 数据，结束回滚或清理

- [ ] **Step 2: 在测试中插入最小 Team/Match**

示例（在 test 内用 async_session 直连插入）：

```python
from app.db.database import async_session
from app.db.models import Team, Match
from datetime import datetime

async with async_session() as db:
    home = Team(name_en="HOME", style_tag="大开大合")
    away = Team(name_en="AWAY", style_tag="大开大合")
    db.add_all([home, away])
    await db.commit()
    await db.refresh(home)
    await db.refresh(away)

    m = Match(kickoff_time=datetime.utcnow(), home_team_id=home.id, away_team_id=away.id)
    db.add(m)
    await db.commit()
    await db.refresh(m)
```

然后用 `m.id` 作为 match_id 调 API，保证测试可重复。

- [ ] **Step 3: 跑完整测试套件**

Run:

```powershell
pytest -q
```

Expected:
- PASS

- [ ] **Step 4: Commit**

```powershell
git add backend/tests
git commit -m "test: add marketflow api tests with db fixtures"
```

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-20-market-flow-plan.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration  
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints  

Which approach?

