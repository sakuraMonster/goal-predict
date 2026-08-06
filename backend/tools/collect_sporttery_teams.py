"""
从竞彩网赛程 API + SportMonks 积分榜采集球队数据
输出: sporttery_teams_raw.json + sportmonks_teams_raw.json
"""
import asyncio
import json
import sys
import os
from collections import defaultdict

# 添加 backend 目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import League
from sqlalchemy import select

JCZQ_API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"

# 联赛映射：竞彩网 leagueAbbName → 目标联赛名（用于匹配 DB leagues 表）
LEAGUE_NAME_MAP = {
    "英超": "英超", "西甲": "西甲", "德甲": "德甲", "意甲": "意甲", "法甲": "法甲",
    "日职": "日职", "韩职": "韩K", "澳超": "澳超", "瑞典超": "瑞典超",
    "挪超": "挪超", "美职": "美职联", "欧冠": "欧冠", "巴甲": "巴甲",
    "芬超": "芬超", "瑞超": "瑞典超",
}


async def collect_sporttery_teams() -> dict:
    """从竞彩网赛程 API 收集所有唯一的 (leagueId, teamId, teamName)"""
    url = f"{JCZQ_API}?poolCode=hhad,had&channel=c"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()

    data = resp.json()
    if not data.get("success"):
        raise Exception(f"API error: {data.get('errorMessage')}")

    # 按联赛分组: {league_abbr: {team_id: team_name}}
    leagues = defaultdict(dict)

    for day_group in data["value"]["matchInfoList"]:
        for match in day_group.get("subMatchList", []):
            league_name = match.get("leagueAbbName", "")
            league_all = match.get("leagueAllName", "")
            league_id = match.get("leagueId", "")

            for side in ["home", "away"]:
                tid = match.get(f"{side}TeamId", "")
                tname = match.get(f"{side}TeamAllName", "")
                if tid and tname:
                    key = f"{league_name}|{league_id}"
                    if key not in leagues:
                        leagues[key] = {"league_abbr": league_name, "league_all": league_all, "league_id": league_id, "teams": {}}
                    leagues[key]["teams"][str(tid)] = tname

    # 转换为列表格式
    result = {}
    for key, info in leagues.items():
        league_abbr = info["league_abbr"]
        teams_list = [{"team_id": tid, "team_name": tname} for tid, tname in info["teams"].items()]
        result[league_abbr] = {
            "league_abbr": league_abbr,
            "league_all": info["league_all"],
            "league_id": info["league_id"],
            "team_count": len(teams_list),
            "teams": teams_list,
        }

    return result


async def collect_sportmonks_teams() -> dict:
    """从 SportMonks 积分榜获取每个联赛的当前赛季完整球队列表"""
    sm = SportMonksClient()
    result = {}

    async with async_session() as db:
        db_result = await db.execute(select(League).where(League.active == True))
        db_leagues = db_result.scalars().all()

    for league in db_leagues:
        sm_league_id = league.sportmonks_id if hasattr(league, 'sportmonks_id') and league.sportmonks_id else None
        if not sm_league_id:
            print(f"  跳过 {league.name_zh}: 无 sportmonks_id")
            continue

        try:
            league_data = await sm.get_league_by_id(sm_league_id)
            seasons = league_data.get("seasons", []) if isinstance(league_data, dict) else []

            # 找当前赛季
            current_season = None
            prev_season = None
            for s in (seasons if isinstance(seasons, list) else []):
                if not isinstance(s, dict):
                    continue
                sid = s.get("id")
                if s.get("is_current_season"):
                    current_season = sid
                if not current_season and sid:
                    prev_season = sid  # 保留最后一个作为降级

            target_season = current_season or prev_season
            if not target_season:
                print(f"  跳过 {league.name_zh}: 无可用赛季")
                continue

            # 获取积分榜
            standings = await sm.get_standings_by_season(target_season)
            teams = {}

            for standing in (standings if isinstance(standings, list) else []):
                if not isinstance(standing, dict):
                    continue
                participant = standing.get("participant", {})
                if isinstance(participant, dict):
                    sm_id = participant.get("id")
                    name = participant.get("name", "")
                    short = participant.get("short_code", "")
                    if sm_id and name:
                        teams[str(sm_id)] = {"name": name, "short_code": short}

            # 补充: get_teams_by_season 获取完整信息
            if not teams:
                try:
                    teams_data = await sm.get_teams_by_season(target_season)
                    for t in (teams_data if isinstance(teams_data, list) else []):
                        if isinstance(t, dict):
                            sm_id = t.get("id")
                            name = t.get("name", "")
                            short = t.get("short_code", "")
                            if sm_id and name:
                                teams[str(sm_id)] = {"name": name, "short_code": short}
                except Exception as e:
                    print(f"  {league.name_zh}: get_teams_by_season 失败: {e}")

            if teams:
                result[league.name_zh] = {
                    "sm_league_id": sm_league_id,
                    "season_id": target_season,
                    "is_current": bool(current_season),
                    "team_count": len(teams),
                    "teams": [{"sm_id": tid, **info} for tid, info in teams.items()],
                }
                print(f"  {league.name_zh}: {len(teams)} 支球队 (season={target_season}, current={bool(current_season)})")
            else:
                print(f"  {league.name_zh}: 未获取到球队数据")

        except Exception as e:
            print(f"  {league.name_zh}: 处理失败: {e}")

    return result


async def main():
    print("=" * 60)
    print("步骤1: 从竞彩网赛程 API 收集球队数据...")
    print("=" * 60)
    jc_teams = await collect_sporttery_teams()

    output_path_jc = os.path.join(os.path.dirname(__file__), "..", "..", "sporttery_teams_raw.json")
    with open(output_path_jc, "w", encoding="utf-8") as f:
        json.dump(jc_teams, f, ensure_ascii=False, indent=2)

    total_teams = sum(v["team_count"] for v in jc_teams.values())
    print(f"\n竞彩网: {len(jc_teams)} 个联赛, {total_teams} 支球队（去重后）")
    print(f"已写入: {output_path_jc}")

    print("\n" + "=" * 60)
    print("步骤2: 从 SportMonks 积分榜获取完整球队列表...")
    print("=" * 60)
    sm_teams = await collect_sportmonks_teams()

    output_path_sm = os.path.join(os.path.dirname(__file__), "..", "..", "sportmonks_teams_raw.json")
    with open(output_path_sm, "w", encoding="utf-8") as f:
        json.dump(sm_teams, f, ensure_ascii=False, indent=2)

    total_sm = sum(v["team_count"] for v in sm_teams.values())
    print(f"\nSportMonks: {len(sm_teams)} 个联赛, {total_sm} 支球队")
    print(f"已写入: {output_path_sm}")


if __name__ == "__main__":
    asyncio.run(main())
