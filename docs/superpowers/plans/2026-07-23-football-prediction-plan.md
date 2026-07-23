# 竞彩足球智能化预测系统 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建竞彩足球 AI 预测系统——从 500.com/SportMonks 采数据，经双模型（LightGBM+Poisson）预测胜平负/让球/进球/比分，输出可视化看板与自动报告。

**Architecture:** FastAPI 后端 + React 18 前端 + PostgreSQL 数据库，APScheduler 定时任务，MLflow 模型管理，Docker Compose 本地部署。

**Tech Stack:** Python 3.12, FastAPI, React 18+TS, Tailwind, ECharts, PostgreSQL, LightGBM, scikit-learn, MLflow, APScheduler, Redis, Docker

## Global Constraints

- Python >= 3.12，React >= 18，TypeScript strict mode
- 项目目录：`e:\zhangxuejun\new-thinking\ricking-03`
- 后端路径：`backend/`，前端路径：`frontend/`
- 数据库：PostgreSQL，SQLAlchemy ORM，所有中文文本用中文
- UI 设计稿路径：`docs/superpowers/specs/ui-mockups/`，开发前必须查看对应设计稿
- UI 配色：羊皮纸暖灰体系（底色 #f5f0e8，强调色 #2d5a3b）
- UI 字体：中文标题思源宋体（Noto Serif SC），正文/数字 Inter
- 所有 API 返回 JSON，字段命名 snake_case
- 单元测试：pytest，前端：vitest

---

## Phase 1: 项目脚手架与数据库

### Task 1.1: Python 后端项目初始化

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/main.py`
- Create: `backend/app/__init__.py`
- Create: `backend/app/db/__init__.py`
- Create: `backend/app/db/database.py`
- Create: `backend/.env.example`

**Interfaces:**
- Produces: FastAPI app instance, SQLAlchemy async engine

- [ ] **Step 1: 创建 requirements.txt**

```txt
fastapi==0.115.0
uvicorn[standard]==0.30.0
sqlalchemy[asyncio]==2.0.35
asyncpg==0.30.0
alembic==1.13.0
pydantic==2.9.0
pydantic-settings==2.5.0
httpx==0.27.0
beautifulsoup4==4.12.3
playwright==1.47.0
apscheduler==3.10.4
redis==5.1.0
lightgbm==4.5.0
scikit-learn==1.5.0
numpy==2.1.0
pandas==2.2.0
mlflow==2.16.0
jinja2==3.1.4
openpyxl==3.1.5
weasyprint==62.3
python-dotenv==1.0.1
pytest==8.3.0
pytest-asyncio==0.24.0
```

- [ ] **Step 2: 创建 backend/app/db/database.py**

```python
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/football_prediction"
)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with async_session() as session:
        yield session
```

- [ ] **Step 3: 创建 backend/main.py**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="竞彩足球预测系统", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
```

- [ ] **Step 4: 创建 backend/.env.example**

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/football_prediction
SPORTMONKS_API_KEY=your_api_key_here
SPORTMONKS_BASE_URL=https://api.sportmonks.com/v3
REDIS_URL=redis://localhost:6379/0
```

- [ ] **Step 5: 验证启动**

```bash
cd backend && pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# 访问 http://localhost:8000/api/health 应返回 {"status":"ok"}
```

---

### Task 1.2: 数据库模型定义

**Files:**
- Create: `backend/app/db/models.py`

**Interfaces:**
- Consumes: `Base` from database.py
- Produces: SQLAlchemy models for all 11 tables

- [ ] **Step 1: 创建 backend/app/db/models.py — Leagues & Teams**

```python
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, Enum, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class League(Base):
    __tablename__ = "leagues"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sportmonks_id = Column(Integer, unique=True, index=True)
    name_zh = Column(String(100), nullable=False)
    name_en = Column(String(100), nullable=False)
    country = Column(String(100))
    season = Column(String(20))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class LeagueAlias(Base):
    __tablename__ = "league_aliases"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    alias_name = Column(String(200), nullable=False)
    source = Column(String(50))  # '500.com' or 'sportmonks'
    is_primary = Column(Boolean, default=False)

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sportmonks_id = Column(Integer, unique=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    name_zh = Column(String(100))
    name_en = Column(String(100), nullable=False)
    short_zh = Column(String(50))
    short_en = Column(String(50))
    logo_url = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

class TeamAlias(Base):
    __tablename__ = "team_aliases"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    alias_name = Column(String(200), nullable=False)
    source = Column(String(50))  # '500.com' or 'sportmonks'
    is_primary = Column(Boolean, default=False)
```

- [ ] **Step 2: 创建 matches / odds / stats 模型（续写 models.py）**

```python
class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True, autoincrement=True)
    jc_match_id = Column(String(50), unique=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    home_team_id = Column(Integer, ForeignKey("teams.id"))
    away_team_id = Column(Integer, ForeignKey("teams.id"))
    kickoff_time = Column(DateTime, nullable=False)
    status = Column(String(20), default="scheduled")  # scheduled/live/finished/cancel
    handicap_line = Column(Float)
    venue = Column(String(200))
    home_score = Column(Integer)
    away_score = Column(Integer)
    half_home_score = Column(Integer)
    half_away_score = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    snapshot_time = Column(DateTime, nullable=False)
    bookmaker = Column(String(100))
    home_win = Column(Float)
    draw = Column(Float)
    away_win = Column(Float)
    handicap_home = Column(Float)
    handicap_line = Column(Float)
    handicap_away = Column(Float)
    over_odds = Column(Float)
    goal_line = Column(Float)
    under_odds = Column(Float)

class TeamSeasonStats(Base):
    __tablename__ = "team_season_stats"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    season = Column(String(20))
    league_id = Column(Integer, ForeignKey("leagues.id"))
    played = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    draws = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    goals_for = Column(Integer, default=0)
    goals_against = Column(Integer, default=0)
    home_wins = Column(Integer, default=0)
    home_draws = Column(Integer, default=0)
    home_losses = Column(Integer, default=0)
    away_wins = Column(Integer, default=0)
    away_draws = Column(Integer, default=0)
    away_losses = Column(Integer, default=0)
    clean_sheets = Column(Integer, default=0)
    failed_to_score = Column(Integer, default=0)
    avg_possession = Column(Float)
    xG = Column(Float)
    xGA = Column(Float)
    xPTS = Column(Float)
    form = Column(String(20))  # "WWDLWD"
```

- [ ] **Step 3: 创建 head_to_head / injuries / predictions / task_logs（续写 models.py）**

```python
class HeadToHead(Base):
    __tablename__ = "head_to_head"
    id = Column(Integer, primary_key=True)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    match_date = Column(DateTime, nullable=False)
    competition = Column(String(200))
    home_score = Column(Integer)
    away_score = Column(Integer)
    is_neutral_venue = Column(Boolean, default=False)

class Injury(Base):
    __tablename__ = "injuries"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    player_name = Column(String(200))
    type = Column(String(50))  # injury/suspension
    reason = Column(String(500))
    start_date = Column(DateTime)
    expected_return = Column(DateTime)
    status = Column(String(20), default="out")

class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, unique=True, index=True)
    model_version = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    # 模型A: 胜平负
    home_prob = Column(Float)
    draw_prob = Column(Float)
    away_prob = Column(Float)
    # 模型A: 让球胜平负
    handicap_home_prob = Column(Float)
    handicap_draw_prob = Column(Float)
    handicap_away_prob = Column(Float)
    # 模型B: 进球数
    expected_goals = Column(Float)
    over_2_5_prob = Column(Float)
    goal_distribution = Column(JSON)  # [0.06, 0.17, 0.24, 0.22, 0.31]
    # 比分推导
    score_top5_json = Column(JSON)  # [{score:"2:1", prob:0.162, result:"home"},...]
    # 报告文本
    summary_text = Column(Text)
    key_factors = Column(Text)
    risk_warning = Column(Text)
    # 元信息
    confidence_level = Column(String(20))  # high/medium/low
    is_cold_match = Column(Boolean, default=False)

class TaskLog(Base):
    __tablename__ = "task_logs"
    id = Column(Integer, primary_key=True)
    task_type = Column(String(50), nullable=False)  # sync_matches/sync_odds/update_teams/retrain
    status = Column(String(20))  # running/success/failed
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    duration_ms = Column(Integer)
    message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 4: 验证模型**

```bash
cd backend
python -c "from app.db.models import Base; print('Models OK, tables:', len(Base.metadata.tables))"
# 应输出: Models OK, tables: 11
```

---

### Task 1.3: Docker Compose 环境搭建

**Files:**
- Create: `docker-compose.yml`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`

**Interfaces:**
- Produces: PostgreSQL + Redis 容器，数据库表已创建

- [ ] **Step 1: 创建 docker-compose.yml**

```yaml
version: "3.9"
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: football_prediction
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
volumes:
  pgdata:
```

- [ ] **Step 2: 启动数据库**

```bash
docker compose up -d
# 等待几秒让数据库就绪
docker compose ps
# 应显示 db 和 redis 均为 Up 状态
```

- [ ] **Step 3: 创建表**

```bash
cd backend
python -c "
import asyncio
from app.db.database import engine, Base
from app.db.models import *

async def init():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('All tables created')

asyncio.run(init())
"
```

- [ ] **Step 4: 验证**

```bash
docker compose exec db psql -U postgres -d football_prediction -c "\dt"
# 应列出 11 张表
```

---

## Phase 2: 数据采集层

### Task 2.1: SportMonks API 客户端

**Files:**
- Create: `backend/app/collector/__init__.py`
- Create: `backend/app/collector/sportmonks/__init__.py`
- Create: `backend/app/collector/sportmonks/client.py`

**Interfaces:**
- Produces: `SportMonksClient` class with methods `get_leagues()`, `get_teams()`, `get_fixtures()`, `get_odds()`, `get_stats()`, `get_head_to_head()`
- Uses: SPORTMONKS_API_KEY from env

- [ ] **Step 1: 创建 SportMonks 客户端基础类**

```python
import os
import httpx
from typing import Optional, Dict, Any, List

class SportMonksClient:
    BASE_URL = "https://api.sportmonks.com/v3"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SPORTMONKS_API_KEY", "")
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
            headers={"Accept": "application/json"},
        )

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        params = params or {}
        params["api_token"] = self.api_key
        response = await self.client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()
```

- [ ] **Step 2: 添加联赛和球队获取方法**

```python
    async def get_all_leagues(self) -> List[Dict]:
        """获取所有联赛"""
        data = await self._get("/leagues", {"include": "country"})
        return data.get("data", [])

    async def get_teams_by_season(self, season_id: int) -> List[Dict]:
        """获取赛季下所有球队"""
        data = await self._get(f"/teams/seasons/{season_id}")
        return data.get("data", [])

    async def get_fixtures_by_date(self, date: str, includes: Optional[str] = None) -> List[Dict]:
        """获取指定日期赛事"""
        params = {"filters": f"fixtureDate:{date}"}
        if includes:
            params["include"] = includes
        data = await self._get("/fixtures", params)
        return data.get("data", [])
```

- [ ] **Step 3: 添加历史数据获取方法**

```python
    async def get_fixtures_by_date_range(self, league_id: int, season_id: int) -> List[Dict]:
        """获取某赛季某联赛所有赛程"""
        params = {
            "filters": f"leagueId:{league_id};seasonId:{season_id}",
            "include": "scores;participants;odds",
        }
        data = await self._get("/fixtures", params)
        return data.get("data", [])

    async def get_team_stats(self, team_id: int, season_id: int) -> Dict:
        """获取球队赛季统计数据"""
        data = await self._get(
            f"/teams/{team_id}",
            {"include": f"statistics.season:{season_id};latest"}
        )
        return data.get("data", {})

    async def get_head_to_head(self, team1_id: int, team2_id: int) -> List[Dict]:
        """获取两队历史交锋"""
        data = await self._get(
            "/fixtures/head-to-head",
            {"firstTeam": str(team1_id), "secondTeam": str(team2_id)}
        )
        return data.get("data", [])
```

---

### Task 2.2: 500.com 爬虫

**Files:**
- Create: `backend/app/collector/scrapers/__init__.py`
- Create: `backend/app/collector/scrapers/jczq_scraper.py`
- Create: `backend/app/collector/scrapers/liansai_scraper.py`

**Interfaces:**
- Produces: `scrape_daily_matches(date)` → List[Dict], `scrape_odds(match_ids)` → List[Dict], `scrape_team_names(league_url)` → List[str]

- [ ] **Step 1: 创建竞彩赛程爬虫 jczq_scraper.py**

```python
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict

JCZQ_BASE = "https://trade.500.com/jczq/"

async def scrape_daily_matches(playid: int = 269) -> List[Dict]:
    """拉取当日竞彩足球赛程"""
    url = f"{JCZQ_BASE}?playid={playid}&g=2"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    matches = []
    # 解析竞彩赛程表格，提取 jc_match_id / 联赛 / 主队 / 客队 / 开赛时间 / 让球数
    for row in soup.select("tr[data-matchid]"):
        match = {
            "jc_match_id": row.get("data-matchid", ""),
            "league_name": "",      # 从 row 解析
            "home_team": "",         # 从 row 解析
            "away_team": "",         # 从 row 解析
            "kickoff_time": "",      # 从 row 解析
            "handicap_line": 0.0,    # 从 row 解析
        }
        matches.append(match)
    return matches
```

> **注意：** 500.com 页面结构可能变化，实际解析逻辑需根据当前 HTML 结构调整。建议先用浏览器 F12 查看实际 DOM，然后编写具体的选择器。上述为接口定义框架。

- [ ] **Step 2: 创建赔率爬虫**

```python
async def scrape_odds(jc_match_ids: List[str]) -> List[Dict]:
    """拉取指定竞彩赛事的赔率数据"""
    odds_data = []
    for mid in jc_match_ids:
        url = f"{JCZQ_BASE}?playid=269&mid={mid}"
        # HTTP 请求 + BeautifulSoup 解析欧赔/亚盘/大小球
        # 返回 [{match_id, bookmaker, home_win, draw, away_win, handicap_line, ...}]
        pass
    return odds_data
```

- [ ] **Step 3: 创建联赛球队名称爬虫 liansai_scraper.py**

```python
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict

LIANSAI_BASE = "https://liansai.500.com"

async def scrape_league_teams(league_path: str) -> Dict[str, List[str]]:
    """从 liansai.500.com 获取某联赛所有球队中文名
    Args:
        league_path: 如 '/zuqiu/yingchao/' 代表英超
    Returns:
        {"league_name": "英格兰超级联赛", "teams": ["曼彻斯特联", "利物浦", ...]}
    """
    url = f"{LIANSAI_BASE}{league_path}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text.replace("gb2312", "utf-8"), "html.parser")
    # 解析球队列表
    return {"league_name": "", "teams": []}
```

---

### Task 2.3: 数据采集 Pipeline

**Files:**
- Create: `backend/app/collector/pipeline.py`

**Interfaces:**
- Consumes: SportMonksClient, scraper functions
- Produces: `SyncPipeline` class: `sync_daily_matches()`, `sync_odds()`, `sync_team_info()`

- [ ] **Step 1: 创建采集 Pipeline 骨架**

```python
from datetime import datetime, timedelta
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.models import Match, OddsSnapshot, TeamSeasonStats, Prediction
from app.collector.sportmonks.client import SportMonksClient
from app.collector.scrapers import jczq_scraper, liansai_scraper

class SyncPipeline:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.sm = SportMonksClient()

    async def sync_daily_matches(self):
        """同步当日+未来3日竞彩赛程"""
        today = datetime.now().date()
        for i in range(4):
            date = today + timedelta(days=i)
            matches = await jczq_scraper.scrape_daily_matches()
            for m in matches:
                # 匹配 SportMonks 中的球队，关联 league_id / team_id
                # INSERT ... ON CONFLICT (jc_match_id) DO UPDATE
                pass
        print(f"赛程同步完成")

    async def sync_odds(self):
        """同步赔率变动"""
        # 查询当日 matches
        # 调用 jczq_scraper.scrape_odds()
        # 批量 INSERT odds_snapshots
        pass

    async def sync_team_info(self):
        """更新球队伤病/阵容/积分排名"""
        # 调用 sm.get_team_stats()
        # 更新 team_season_stats
        pass
```

---

## Phase 3: 名称映射与数据清洗

### Task 3.1: 名称映射引擎

**Files:**
- Create: `backend/app/collector/cleaner.py`

**Interfaces:**
- Produces: `NameMatcher` class with L1/L2/L3 matching logic
- Produces Functions: `match_using_translation()`, `match_using_edit_distance()`

- [ ] **Step 1: 三层匹配引擎**

```python
from difflib import SequenceMatcher

# L1: 常见地名翻译表
CITY_TRANSLATIONS = {
    "曼彻斯特": "Manchester", "伦敦": "London", "利物浦": "Liverpool",
    "巴塞罗那": "Barcelona", "马德里": "Madrid", "巴黎": "Paris",
    "慕尼黑": "Munich", "多特蒙德": "Dortmund", "米兰": "Milan",
    "都灵": "Turin", "那不勒斯": "Napoli", "罗马": "Roma",
}

class NameMatcher:
    @staticmethod
    def match_l1(chinese_name: str, candidates: list) -> dict | None:
        """L1: 翻译表精确匹配"""
        zh_lower = chinese_name.lower()
        for city_zh, city_en in CITY_TRANSLATIONS.items():
            if city_zh in zh_lower:
                for c in candidates:
                    if city_en.lower() in c["name_en"].lower():
                        return {"level": "L1", "match": c, "confidence": 1.0}
        return None

    @staticmethod
    def match_l2(chinese_name: str, candidates: list) -> list:
        """L2: 编辑距离模糊匹配，返回按相似度排序的候选"""
        results = []
        for c in candidates:
            ratio = SequenceMatcher(None, chinese_name.lower(), c["name_en"].lower()).ratio()
            results.append({**c, "similarity": ratio})
        return sorted(results, key=lambda x: x["similarity"], reverse=True)
```

---

### Task 3.2: 映射持久化

**Files:**
- Create: `backend/app/api/mappings.py`

**Interfaces:**
- Consumes: NameMatcher
- Produces: API endpoints for mapping CRUD

- [ ] **Step 1: 映射管理 API**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.db.models import Team, TeamAlias, League, LeagueAlias

router = APIRouter(prefix="/api/mappings", tags=["mappings"])

@router.get("/pending")
async def get_pending_teams(db: AsyncSession = Depends(get_db)):
    """获取待确认映射队列"""
    # 查询没有 name_zh 但有 500.com 来源数据的 teams
    pass

@router.post("/confirm")
async def confirm_mapping(payload: dict, db: AsyncSession = Depends(get_db)):
    """确认映射: {team_id, alias_name, source}"""
    alias = TeamAlias(
        team_id=payload["team_id"],
        alias_name=payload["alias_name"],
        source=payload["source"],
        is_primary=True
    )
    db.add(alias)
    await db.commit()
    return {"status": "ok"}

@router.post("/add-alias")
async def add_alias(payload: dict, db: AsyncSession = Depends(get_db)):
    """添加别名"""
    # 支持 type=team 或 type=league
    pass
```

---

## Phase 4: 预测模型

### Task 4.1: 特征工程 Pipeline

**Files:**
- Create: `backend/app/predictor/__init__.py`
- Create: `backend/app/predictor/features.py`

**Interfaces:**
- Produces: `FeatureEngineer.extract_features(match_id)` → pd.DataFrame (84 features)

- [ ] **Step 1: 特征工程类框架**

```python
import pandas as pd
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

class FeatureEngineer:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def extract_features(self, match_id: int) -> pd.DataFrame:
        """从数据库提取约 84 个特征，返回单行 DataFrame"""
        # 1. 查询 match 基本信息
        # 2. 查询两队的 team_season_stats
        # 3. 查询 head_to_head
        # 4. 查询 odds_snapshots
        # 5. 计算各项特征
        features = {
            # 类别A: 球队基础战力(12)
            "home_win_rate": 0.0,
            "home_draw_rate": 0.0,
            "away_win_rate": 0.0,
            "home_goals_avg": 0.0,
            "away_goals_avg": 0.0,
            "home_goals_against_avg": 0.0,
            "away_goals_against_avg": 0.0,
            "home_home_win_rate": 0.0,
            "away_away_win_rate": 0.0,
            "rest_days_diff": 0,
            # 类别B: 交锋(8) - 略
            # 类别C: 赔率信号(7)
            "odds_home_initial": 0.0,
            "odds_home_current": 0.0,
            "odds_movement": 0.0,  # 赔率变动幅度
            # ... 继续补充到约84个特征
        }
        return pd.DataFrame([features])
```

---

### Task 4.2: 模型A — 胜平负 + 让胜平负 (LightGBM)

**Files:**
- Create: `backend/app/predictor/models/model_a.py`
- Create: `backend/app/predictor/trainer.py`

**Interfaces:**
- Produces: `ModelA.predict(features)` → (home_prob, draw_prob, away_prob, handicap_home_prob, handicap_draw_prob, handicap_away_prob)
- Produces: `trainer.train_model_a()` 从历史数据训练

- [ ] **Step 1: 模型A类定义**

```python
import lightgbm as lgb
import numpy as np
import pandas as pd

class ModelA:
    """LightGBM 多任务模型：同时输出胜平负 + 让球胜平负"""
    def __init__(self):
        self.model_wl = None  # 胜平负分类器
        self.model_hcp = None  # 让球胜平负分类器

    def predict(self, features: pd.DataFrame):
        """预测并返回概率"""
        probs_wl = self.model_wl.predict_proba(features)[0]  # [home, draw, away]
        probs_hcp = self.model_hcp.predict_proba(features)[0] if self.model_hcp else None
        return {
            "home_prob": float(probs_wl[0]),
            "draw_prob": float(probs_wl[1]),
            "away_prob": float(probs_wl[2]),
            "handicap_home_prob": float(probs_hcp[0]) if probs_hcp is not None else None,
            "handicap_draw_prob": float(probs_hcp[1]) if probs_hcp is not None else None,
            "handicap_away_prob": float(probs_hcp[2]) if probs_hcp is not None else None,
        }

    def train(self, X: pd.DataFrame, y_wl: np.ndarray, y_hcp: np.ndarray):
        """训练模型"""
        self.model_wl = lgb.LGBMClassifier(
            objective="multiclass", num_class=3,
            n_estimators=200, learning_rate=0.05, max_depth=6,
            random_state=42, verbose=-1
        )
        self.model_wl.fit(X, y_wl)

        self.model_hcp = lgb.LGBMClassifier(
            objective="multiclass", num_class=3,
            n_estimators=200, learning_rate=0.05, max_depth=6,
            random_state=42, verbose=-1
        )
        self.model_hcp.fit(X, y_hcp)
```

---

### Task 4.3: 模型B — 进球数 (Poisson) + 比分推导

**Files:**
- Create: `backend/app/predictor/models/model_b.py`

**Interfaces:**
- Produces: `ModelB.predict(features)` → (expected_goals, goal_distribution, score_top5)
- Produces: `ModelB.train(X, y_goals)` 训练泊松回归

- [ ] **Step 1: 模型B类定义**

```python
import numpy as np
import pandas as pd
from sklearn.linear_model import PoissonRegressor
from scipy.stats import poisson

class ModelB:
    """Poisson Regression 进球数预测 + 比分推导"""
    def __init__(self):
        self.model = None

    def predict(self, features: pd.DataFrame):
        """预测进球数分布和比分"""
        lambda_val = self.model.predict(features)[0]
        lambda_val = max(lambda_val, 0.1)

        # 进球数分布 (0-4+球)
        goal_probs = [poisson.pmf(k, lambda_val) for k in range(5)]
        goal_probs[4] = 1 - poisson.cdf(3, lambda_val)  # 4+球概率
        goal_probs = [float(p) for p in goal_probs]

        # 比分推导：需要外部提供胜平负概率来约束
        # 此处返回进球期望，比分在 PredictionPipeline 中联合推导
        return {
            "expected_goals": float(lambda_val),
            "goal_distribution": goal_probs,
            "over_2_5_prob": float(1 - poisson.cdf(2, lambda_val)),
        }

    def train(self, X: pd.DataFrame, y: np.ndarray):
        self.model = PoissonRegressor(alpha=1e-3, max_iter=500)
        self.model.fit(X, y)
```

---

### Task 4.4: 预测 Pipeline & 模型管理

**Files:**
- Create: `backend/app/predictor/pipeline.py`
- Create: `backend/app/predictor/trainer.py`

**Interfaces:**
- Produces: `PredictionPipeline.predict(match_id)` → 完整 Prediction 对象
- Produces: `train_all()` 触发全量重训练

- [ ] **Step 1: 预测 Pipeline（整合模型A+B+比分推导）**

```python
import json
import numpy as np
import mlflow
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB
from app.db.models import Prediction

class PredictionPipeline:
    def __init__(self, db):
        self.db = db
        self.feature_engineer = FeatureEngineer(db)
        self.model_a = ModelA()
        self.model_b = ModelB()

    async def predict(self, match_id: int) -> dict:
        features = await self.feature_engineer.extract_features(match_id)
        result_a = self.model_a.predict(features)
        result_b = self.model_b.predict(features)

        # 比分推导：联合 A 的胜负倾向 + B 的进球期望
        score_top5 = self._derive_scores(
            result_a["home_prob"], result_a["draw_prob"], result_a["away_prob"],
            result_b["expected_goals"]
        )

        # 冷门判定
        is_cold = self._is_cold_match(result_a, features)

        return {
            **result_a, **result_b,
            "score_top5_json": score_top5,
            "is_cold_match": is_cold,
            "confidence_level": self._calc_confidence(result_a, is_cold),
        }

    def _derive_scores(self, home_p, draw_p, away_p, expected_goals):
        """基于胜负倾向+进球期望计算最可能比分"""
        scores = []
        max_goals = min(int(expected_goals) + 3, 6)
        for hg in range(max_goals + 1):
            for ag in range(max_goals + 1):
                if hg + ag == 0:
                    continue
                # 联合概率 = 泊松概率(hg)*泊松概率(ag) * 倾向权重
                p = (poisson.pmf(hg + ag, expected_goals) *
                     (home_p if hg > ag else draw_p if hg == ag else away_p))
                result = "home" if hg > ag else "draw" if hg == ag else "away"
                scores.append({"score": f"{hg}:{ag}", "prob": round(float(p), 4), "result": result})

        scores.sort(key=lambda x: x["prob"], reverse=True)
        return scores[:5]

    def _is_cold_match(self, result_a, features) -> bool:
        max_prob = max(result_a["home_prob"], result_a["draw_prob"], result_a["away_prob"])
        return max_prob < 0.40

    def _calc_confidence(self, result_a, is_cold) -> str:
        max_prob = max(result_a["home_prob"], result_a["draw_prob"], result_a["away_prob"])
        if is_cold or max_prob < 0.45:
            return "low"
        if max_prob > 0.60:
            return "high"
        return "medium"
```

---

## Phase 5: 定时任务

### Task 5.1: APScheduler 配置

**Files:**
- Create: `backend/app/scheduler.py`

**Interfaces:**
- Produces: 4 个定时任务，Redis 分布式锁防重复

- [ ] **Step 1: 调度器配置**

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import redis.asyncio as redis
from app.collector.pipeline import SyncPipeline

scheduler = AsyncIOScheduler()

# 赛程同步：每日 09:00, 12:00
scheduler.add_job(
    SyncPipeline.sync_daily_matches,
    CronTrigger(hour=9, minute=0),
    id="sync_matches_09",
)
scheduler.add_job(
    SyncPipeline.sync_daily_matches,
    CronTrigger(hour=12, minute=0),
    id="sync_matches_12",
)

# 赔率更新：每日 09:00, 14:00, 18:00
for hour in [9, 14, 18]:
    scheduler.add_job(
        SyncPipeline.sync_odds,
        CronTrigger(hour=hour, minute=0),
        id=f"sync_odds_{hour}",
    )

# 球队信息：每日 03:00
scheduler.add_job(
    SyncPipeline.sync_team_info,
    CronTrigger(hour=3, minute=0),
    id="update_teams",
)

# 模型重训练：每周一 04:00
scheduler.add_job(
    SyncPipeline.retrain_models,
    CronTrigger(day_of_week="mon", hour=4, minute=0),
    id="retrain",
)
```

---

## Phase 6: 后端 API 层

### Task 6.1: 赛事与预测 API

**Files:**
- Create: `backend/app/api/matches.py`
- Create: `backend/app/api/predictions.py`

**Interfaces:**
- Produces: REST endpoints for matches and predictions

- [ ] **关键端点实现**

```python
# matches.py
@router.get("/api/matches")
async def list_matches(date: str = None, league_id: int = None, db=Depends(get_db)):
    """赛事列表（含预测摘要），支持日期和联赛筛选"""
    pass

@router.get("/api/matches/{match_id}")
async def get_match_detail(match_id: int, db=Depends(get_db)):
    """单场完整详情：对阵信息 + 赔率历史 + 预测结果 + 报告摘要"""
    pass

@router.get("/api/matches/dates")
async def get_match_dates(db=Depends(get_db)):
    """左侧日期列表：未来3日 + 每日场次数"""
    pass

@router.get("/api/matches/{match_id}/odds-history")
async def get_odds_history(match_id: int, db=Depends(get_db)):
    pass

@router.get("/api/matches/{match_id}/h2h")
async def get_h2h(match_id: int, db=Depends(get_db)):
    pass

# predictions.py
@router.get("/api/predictions/{match_id}")
async def get_prediction(match_id: int, db=Depends(get_db)):
    """单场预测完整数据（含模型A/B全部输出）"""
    pass

@router.get("/api/predictions/review")
async def get_review(days: int = 30, db=Depends(get_db)):
    """复盘统计：准确率趋势/联赛/置信度分档"""
    pass

@router.get("/api/predictions/pnl")
async def get_pnl(days: int = 30, db=Depends(get_db)):
    """盈亏模拟数据"""
    pass
```

---

### Task 6.2: 报告与系统管理 API

**Files:**
- Create: `backend/app/api/reports.py`
- Create: `backend/app/api/admin.py`

---

### Task 6.3: API 注册到 main.py

**Files:**
- Modify: `backend/main.py`

```python
from app.api import matches, predictions, reports, admin, mappings

app.include_router(matches.router)
app.include_router(predictions.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(mappings.router)
```

---

## Phase 7: 前端基础

### Task 7.1: React 项目初始化

**Files:**
- Create: `frontend/` (Vite + React + TypeScript)

```bash
cd frontend
npm create vite@latest . -- --template react-ts
npm install
npm install axios react-router-dom echarts echarts-for-react tailwindcss @tailwindcss/vite
```

### Task 7.2: Tailwind 配置 — 羊皮纸主题

**文件：** `frontend/tailwind.config.js`

```js
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        parchment: { DEFAULT: "#f5f0e8", dark: "#ede6d9", light: "#f9f6ef" },
        ink: { DEFAULT: "#3d3628", muted: "#6b5e4a", light: "#937b5c" },
        moss: { DEFAULT: "#2d5a3b" },
        amber: { DEFAULT: "#c77600" },
        rust: { DEFAULT: "#8b5a2c" },
      },
      fontFamily: {
        heading: ['"Noto Serif SC"', "Georgia", "serif"],
        body: ['"Inter"', "system-ui", "sans-serif"],
      },
    },
  },
};
```

### Task 7.3: API 客户端

**文件：** `frontend/src/api/client.ts`

```typescript
import axios from "axios";

const api = axios.create({ baseURL: "http://localhost:8000/api" });

export const getMatches = (params: { date?: string; league_id?: number }) =>
  api.get("/matches", { params }).then((r) => r.data);

export const getMatchDetail = (id: number) =>
  api.get(`/matches/${id}`).then((r) => r.data);

export const getPrediction = (matchId: number) =>
  api.get(`/predictions/${matchId}`).then((r) => r.data);
```

---

## Phase 8: 前端页面实现

> **每个页面实现前必须先打开对应 UI 设计稿文件查看视觉效果。**
> 设计稿路径：`docs/superpowers/specs/ui-mockups/`

### Task 8.1: 赛事总览 Dashboard

**参考设计稿：** `dashboard-layout.html` + `dashboard-tabs.html`

**文件：**
- Create: `frontend/src/pages/Dashboard.tsx`
- Create: `frontend/src/components/MatchCard.tsx`
- Create: `frontend/src/components/ProbBar.tsx`
- Create: `frontend/src/components/Skeleton.tsx`
- Create: `frontend/src/components/EmptyState.tsx`

实现要点：左侧日期/联赛筛选 + 三 Tab（全部/冷门预警/热门推荐）+ 3 列卡片网格

### Task 8.2: 单场详情 MatchDetail

**参考设计稿：** `match-detail-v3.html`

**文件：**
- Create: `frontend/src/pages/MatchDetail.tsx`
- Create: `frontend/src/components/RadarChart.tsx`
- Create: `frontend/src/components/OddsTrend.tsx`

实现要点：对阵概要 → 左底座 + 右预测（模型A/B双板块）→ 底部报告摘要

### Task 8.3: 预测报告 Report

**参考设计稿：** `report-page.html`

### Task 8.4: 复盘统计 Review

**参考设计稿：** `review-page.html`

### Task 8.5: 名称映射管理 Mapping

**参考设计稿：** `mapping-v2.html`

### Task 8.6: 系统管理 Admin

**参考设计稿：** `admin-page.html`

---

## Phase 9: 集成与部署

### Task 9.1: 路由与导航

**文件：** `frontend/src/App.tsx`

React Router 路由表（按顶部导航 4 个一级入口组织）

### Task 9.2: Docker Compose 集成

更新 `docker-compose.yml` 加入 backend 和 frontend 服务。

---

## 开发优先级顺序

1. Phase 1: 项目脚手架与数据库（Task 1.1 → 1.2 → 1.3）
2. Phase 2: 数据采集层（Task 2.1 → 2.2 → 2.3）
3. Phase 3: 名称映射（Task 3.1 → 3.2）
4. Phase 4: 预测模型（Task 4.1 → 4.2 → 4.3 → 4.4）
5. Phase 5: 定时任务（Task 5.1）
6. Phase 6: 后端 API（Task 6.1 → 6.2 → 6.3）
7. Phase 7: 前端基础（Task 7.1 → 7.2 → 7.3）
8. Phase 8: 前端页面（Task 8.1 → 8.2 → 8.3 → 8.4 → 8.5 → 8.6）
9. Phase 9: 集成部署（Task 9.1 → 9.2）
