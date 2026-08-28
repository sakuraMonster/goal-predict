"""
SportMonks v3 Football API 客户端
Base URL: https://api.sportmonks.com/v3/football
Auth: Authorization header (raw token, 非 Bearer)
"""
import os
import asyncio
import httpx
from urllib.parse import quote
from typing import Optional, Dict, Any, List

class SportMonksClient:
    BASE_URL = "https://api.sportmonks.com/v3/football"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SPORTMONKS_API_KEY", "")
        self._last_request = 0.0  # 上次请求时间戳
        self._min_interval = 2.0  # 最小请求间隔（秒），免费套餐 180/min
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
            headers={"Accept": "application/json"},
        )

    async def _rate_limit(self):
        """请求限速：确保两次请求间隔不小于 _min_interval"""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = asyncio.get_event_loop().time()

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """通用 GET 请求（含限速 + 429 重试）"""
        params = params or {}
        params["api_token"] = self.api_key
        await self._rate_limit()
        for attempt in range(2):
            response = await self.client.get(path, params=params)
            if response.status_code == 429:
                wait = 5 * (2 ** attempt)  # 5s, 10s
                print(f"[SM] 429 rate limited, waiting {wait}s...", flush=True)
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        response.raise_for_status()  # 最终一次仍失败则抛出

    async def _get_paginated(self, path: str, params: Optional[Dict] = None, max_pages: int = 10) -> List[Dict]:
        """分页获取全部数据"""
        params = params or {}
        params.setdefault("per_page", 50)
        all_data = []
        for page in range(1, max_pages + 1):
            params["page"] = page
            data = await self._get(path, params)
            items = data.get("data", [])
            all_data.extend(items)
            pagination = data.get("pagination", {})
            if not pagination.get("has_more", False):
                break
        return all_data

    async def close(self):
        await self.client.aclose()

    # ───────────── Fixtures ─────────────

    async def get_fixture_by_id(self, fixture_id: int, includes: Optional[str] = None) -> Dict:
        """按 ID 查询单场赛事"""
        params = {}
        if includes:
            params["include"] = includes
        data = await self._get(f"/fixtures/{fixture_id}", params)
        return data.get("data", {})

    async def get_fixtures_by_date(self, date: str, includes: Optional[str] = None) -> List[Dict]:
        """按日期查询赛事，date 格式 YYYY-MM-DD"""
        params = {}
        if includes:
            params["include"] = includes
        data = await self._get_paginated(f"/fixtures/date/{date}", params)
        return data

    async def get_fixtures_between(self, date_from: str, date_to: str, includes: Optional[str] = None) -> List[Dict]:
        """按日期范围查询赛事"""
        params = {}
        if includes:
            params["include"] = includes
        data = await self._get_paginated(f"/fixtures/between/{date_from}/{date_to}", params)
        return data

    async def get_fixtures_between_for_team(
        self, date_from: str, date_to: str, team_id: int, includes: Optional[str] = None
    ) -> List[Dict]:
        params = {}
        if includes:
            params["include"] = includes
        data = await self._get_paginated(f"/fixtures/between/{date_from}/{date_to}/{team_id}", params)
        return data

    async def search_fixtures(self, name: str, includes: Optional[str] = None) -> List[Dict]:
        params = {}
        if includes:
            params["include"] = includes
        data = await self._get_paginated(f"/fixtures/search/{quote(name)}", params)
        return data

    # ───────────── Leagues ─────────────

    async def get_all_leagues(self) -> List[Dict]:
        data = await self._get_paginated("/leagues", {"include": "country"})
        return data

    async def get_league_by_id(self, league_id: int) -> Dict:
        data = await self._get(f"/leagues/{league_id}", {"include": "country;seasons"})
        return data.get("data", {})

    # ───────────── Teams ─────────────

    async def get_teams_by_season(self, season_id: int) -> List[Dict]:
        data = await self._get_paginated(f"/teams/seasons/{season_id}")
        return data

    async def get_team_by_id(self, team_id: int, includes: Optional[str] = None) -> Dict:
        params = {}
        if includes:
            params["include"] = includes
        data = await self._get(f"/teams/{team_id}", params)
        return data.get("data", {})

    async def search_teams(self, name: str) -> List[Dict]:
        """按名称搜索球队（用于匹配未知球队到 SportMonks）"""
        data = await self._get(f"/teams/search/{quote(name)}")
        return data.get("data", [])

    # ───────────── Odds ─────────────

    async def get_odds_pre_match(self, fixture_id: int) -> List[Dict]:
        """获取赛前赔率（含 1X2、亚盘、大小球等所有 market）"""
        data = await self._get(f"/odds/pre-match/fixtures/{fixture_id}", {"include": "bookmaker"})
        return data.get("data", [])

    async def get_odds_inplay(self, fixture_id: int) -> List[Dict]:
        """获取进行中赔率"""
        data = await self._get(f"/odds/inplay/fixtures/{fixture_id}")
        return data.get("data", [])

    # ───────────── Standings ─────────────

    async def get_standings_by_season(self, season_id: int) -> List[Dict]:
        """获取赛季积分榜"""
        data = await self._get(f"/standings/seasons/{season_id}")
        return data.get("data", [])

    # ───────────── Statistics ─────────────

    async def get_statistics_by_season_team(self, season_id: int, team_id: int) -> List[Dict]:
        """获取球队赛季详细统计数据（含 details 数组）"""
        if season_id:
            data = await self._get(f"/statistics/seasons/{season_id}/teams/{team_id}")
        else:
            data = await self._get(f"/statistics/seasons/teams/{team_id}")
        return data.get("data", [])

    async def get_statistics_by_participant(self, participant_id: int) -> List[Dict]:
        """通过 participant ID 获取球队赛季详细统计数据"""
        data = await self._get(f"/statistics/seasons/participants/{participant_id}")
        return data.get("data", [])

    # ───────────── Injuries ─────────────

    async def get_injuries(self) -> List[Dict]:
        """获取所有伤病信息"""
        data = await self._get_paginated("/injuries")
        return data

    # ───────────── Head to Head ─────────────

    async def get_head_to_head(self, team1_id: int, team2_id: int) -> List[Dict]:
        """获取两队历史交锋（最近 2 年）"""
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(days=730)
        data = await self._get(
            f"/fixtures/head-to-head/{team1_id}/{team2_id}",
            {"include": "participants;scores"}
        )
        return data.get("data", [])

    # ───────────── Predictions ─────────────

    async def get_predictions_by_fixture(self, fixture_id: int) -> Dict:
        """获取 SportMonks 官方预测概率"""
        data = await self._get(f"/predictions/probabilities/fixtures/{fixture_id}")
        return data.get("data", {})
