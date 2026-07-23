import os
import httpx
from typing import Optional, Dict, Any, List

class SportMonksClient:
    """SportMonks v3 API 客户端，封装常用数据获取方法"""
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

    async def get_all_leagues(self) -> List[Dict]:
        """获取所有可用联赛"""
        data = await self._get("/leagues", {"include": "country"})
        return data.get("data", [])

    async def get_league_by_id(self, league_id: int) -> Dict:
        """获取单个联赛详情"""
        data = await self._get(f"/leagues/{league_id}", {"include": "country;seasons"})
        return data.get("data", {})

    async def get_teams_by_season(self, season_id: int) -> List[Dict]:
        """获取赛季下所有球队"""
        data = await self._get(f"/teams/seasons/{season_id}")
        return data.get("data", [])

    async def get_team_by_id(self, team_id: int, includes: Optional[str] = None) -> Dict:
        """获取球队详情"""
        params = {}
        if includes:
            params["include"] = includes
        data = await self._get(f"/teams/{team_id}", params)
        return data.get("data", {})

    async def get_fixtures_by_date(self, date: str, includes: Optional[str] = None) -> List[Dict]:
        """获取指定日期赛事，date 格式 YYYY-MM-DD"""
        params = {"filters": f"fixtureDate:{date}"}
        if includes:
            params["include"] = includes
        data = await self._get("/fixtures", params)
        return data.get("data", [])

    async def get_fixtures_by_date_range(self, league_id: int, season_id: int) -> List[Dict]:
        """获取某赛季某联赛所有赛程"""
        params = {
            "filters": f"leagueId:{league_id};seasonId:{season_id}",
            "include": "scores;participants",
        }
        data = await self._get("/fixtures", params)
        return data.get("data", [])

    async def get_fixture_by_id(self, fixture_id: int, includes: Optional[str] = None) -> Dict:
        """获取单场赛事详情"""
        params = {}
        if includes:
            params["include"] = includes
        data = await self._get(f"/fixtures/{fixture_id}", params)
        return data.get("data", {})

    async def get_team_stats(self, team_id: int, season_id: int) -> Dict:
        """获取球队赛季统计数据"""
        data = await self._get(
            f"/teams/{team_id}",
            {"include": f"statistics.season:{season_id};latest"}
        )
        return data.get("data", {})

    async def get_head_to_head(self, team1_id: int, team2_id: int) -> List[Dict]:
        """获取两队历史交锋记录"""
        data = await self._get(
            "/fixtures/head-to-head",
            {"firstTeam": str(team1_id), "secondTeam": str(team2_id)}
        )
        return data.get("data", [])

    async def get_odds_by_fixture(self, fixture_id: int) -> List[Dict]:
        """获取赛事赔率数据"""
        data = await self._get(f"/odds/fixtures/{fixture_id}")
        return data.get("data", [])

    async def get_standings(self, season_id: int) -> List[Dict]:
        """获取赛季积分榜"""
        data = await self._get(f"/standings/seasons/{season_id}")
        return data.get("data", [])

    async def get_injuries(self, team_id: int) -> List[Dict]:
        """获取球队伤病信息"""
        data = await self._get(f"/injuries/teams/{team_id}")
        return data.get("data", [])
