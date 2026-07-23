"""
liansai.500.com 联赛球队名称爬虫
数据源: https://liansai.500.com
用于获取联赛球队的中文名称列表
"""
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict

LIANSAI_BASE = "https://liansai.500.com"

# 已知联赛路径映射（可根据需要扩展）
LEAGUE_PATHS = {
    "英超": "/zuqiu/yingchao/",
    "西甲": "/zuqiu/xijia/",
    "德甲": "/zuqiu/dejia/",
    "意甲": "/zuqiu/yijia/",
    "法甲": "/zuqiu/fajia/",
    "日职": "/zuqiu/rizhi/",
    "韩职": "/zuqiu/hanzhi/",
    "中超": "/zuqiu/zhongchao/",
    "澳超": "/zuqiu/aochao/",
    "瑞典超": "/zuqiu/ruidianchao/",
    "挪超": "/zuqiu/nuochao/",
    "美职联": "/zuqiu/meizhi/",
}


async def scrape_league_teams(league_path: str) -> Dict[str, List[str]]:
    """
    从 liansai.500.com 获取某联赛所有球队中文名
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

    # liansai.500.com 使用 gb2312 编码
    resp.encoding = "gb2312"
    soup = BeautifulSoup(resp.text, "html.parser")

    league_name = ""
    teams = []

    # 解析球队列表（页面结构需要实际 DOM 分析后调整）
    for team_el in soup.select(".team_name"):
        name = team_el.get_text(strip=True)
        if name:
            teams.append(name)

    return {
        "league_name": league_name,
        "teams": teams
    }


async def scrape_all_known_leagues() -> Dict[str, Dict]:
    """爬取所有已知联赛的球队列表"""
    results = {}
    for league_name, path in LEAGUE_PATHS.items():
        try:
            result = await scrape_league_teams(path)
            results[league_name] = result
        except Exception as e:
            print(f"爬取联赛 {league_name} 失败: {e}")
    return results
