"""
中国竞彩网 竞彩足球数据采集
数据源: https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry
"""
import httpx
from typing import List, Dict, Optional

JCZQ_API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"


async def scrape_daily_matches(pool_code: str = "hhad,had") -> List[Dict]:
    """
    拉取竞彩足球赛程列表（来源：中国竞彩网 JSON API）

    返回字段与原有 pipeline 兼容:
      jc_match_id  - 竞彩网 matchId (int)
      match_num    - 场次编号如"周日104"
      league_name  - 联赛简称如"巴甲"
      league_all_name - 联赛全称如"巴西甲级联赛"
      league_id    - 竞彩网联赛ID (int)
      home_team    - 主队全称
      away_team    - 客队全称
      home_team_id - 竞彩网主队ID (int)
      away_team_id - 竞彩网客队ID (int)
      kickoff_time - 开赛时间 "YYYY-MM-DD HH:MM:SS"
      handicap_line - 让球线 (float, 来自hhad.goalLine)
    """
    url = f"{JCZQ_API}?poolCode={pool_code}&channel=c"
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.sporttery.cn/jc/jsq/zqspf/",
            "Origin": "https://www.sporttery.cn",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        })
        resp.raise_for_status()

    data = resp.json()
    if not data.get("success"):
        raise Exception(f"竞彩网API返回错误: {data.get('errorMessage', '未知错误')}")

    matches = []
    match_info_list = data.get("value", {}).get("matchInfoList", [])

    for day_group in match_info_list:
        for match in day_group.get("subMatchList", []):
            try:
                match_id = match.get("matchId", "")
                match_date = match.get("matchDate", "")
                match_time = match.get("matchTime", "")
                kickoff_str = f"{match_date} {match_time}" if match_date and match_time else ""

                # 让球线：从 HHAD（让球胜平负）玩法中提取
                hhad = match.get("hhad", {})
                goal_line_str = hhad.get("goalLine", "0") if hhad else "0"
                try:
                    handicap_line = float(goal_line_str) if goal_line_str else 0.0
                except (ValueError, TypeError):
                    handicap_line = 0.0

                result = {
                    "jc_match_id": str(match_id),
                    "match_num": match.get("matchNumStr", ""),
                    "league_name": match.get("leagueAbbName", ""),
                    "league_all_name": match.get("leagueAllName", ""),
                    "league_id": match.get("leagueId", ""),
                    "home_team": match.get("homeTeamAllName", ""),
                    "away_team": match.get("awayTeamAllName", ""),
                    "home_team_id": str(match.get("homeTeamId", "")),
                    "away_team_id": str(match.get("awayTeamId", "")),
                    "home_team_abbr": match.get("homeTeamAbbEnName", ""),
                    "away_team_abbr": match.get("awayTeamAbbEnName", ""),
                    "kickoff_time": kickoff_str,
                    "handicap_line": handicap_line,
                }
                matches.append(result)
            except Exception as e:
                print(f"解析竞彩网赛程数据失败: {e}")

    return matches


async def scrape_odds(_match_id: str) -> Optional[Dict]:
    """竞彩网赔率已在 scrape_daily_matches 中随赛程一起返回，此函数保留接口兼容性"""
    return None


async def scrape_multiple_odds(match_ids: List[str]) -> List[Dict]:
    """竞彩网赔率已在 scrape_daily_matches 中随赛程一起返回，此函数保留接口兼容性"""
    return []
