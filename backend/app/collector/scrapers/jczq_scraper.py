"""
500.com 竞彩足球数据爬虫
数据源: https://trade.500.com/jczq/
"""
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from datetime import datetime

JCZQ_BASE = "https://trade.500.com/jczq/"


async def scrape_daily_matches(playid: int = 269) -> List[Dict]:
    """
    拉取当日竞彩足球赛程列表
    Args:
        playid: 玩法ID，269=混合过关
    Returns:
        [{jc_match_id, league_name, home_team, away_team, kickoff_time, handicap_line}, ...]
    """
    url = f"{JCZQ_BASE}?playid={playid}&g=2"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    matches = []

    # 500.com 竞彩页面使用 JavaScript 渲染，实际数据结构需根据页面分析调整
    # 此为基础框架，列出需要的字段
    for row in soup.select("tr[data-matchid]"):
        try:
            match = {
                "jc_match_id": row.get("data-matchid", ""),
                "league_name": "",
                "home_team": "",
                "away_team": "",
                "kickoff_time": "",
                "handicap_line": 0.0,
            }
            matches.append(match)
        except Exception as e:
            print(f"解析赛程行失败: {e}")
            continue

    return matches


async def scrape_odds(match_id: str) -> Optional[Dict]:
    """
    拉取单场赛事的赔率数据
    Args:
        match_id: 竞彩赛事编号
    Returns:
        {match_id, bookmakers: [{name, home_win, draw, away_win, handicap_home, handicap_line, handicap_away}]}
    """
    url = f"{JCZQ_BASE}?playid=269&mid={match_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

    odds_data = {
        "match_id": match_id,
        "bookmakers": []
    }

    soup = BeautifulSoup(resp.text, "html.parser")
    # 解析博彩公司赔率表
    # 页面结构需要实际 DOM 分析后填充

    return odds_data


async def scrape_multiple_odds(match_ids: List[str]) -> List[Dict]:
    """批量拉取多场赛事赔率"""
    results = []
    for mid in match_ids:
        try:
            odds = await scrape_odds(mid)
            if odds:
                results.append(odds)
        except Exception as e:
            print(f"拉取赔率失败 match_id={mid}: {e}")
    return results
