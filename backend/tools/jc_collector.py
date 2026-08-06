"""竞彩网数据采集器：拉取 HHAD（让球胜平负）+ HAD（胜平负）赔率，匹配本地比赛"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date, timedelta, datetime
import requests
from app.db.database import async_session
from app.db.models import Match, Team, OddsSnapshot
from sqlalchemy import select, update

API_BASE = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.sporttery.cn/",
}

POOL_MAP = {"HHAD": "让球胜平负", "HAD": "胜平负"}


def fetch_jc(date_str: str, pool_code: str) -> list:
    """拉取竞彩数据，返回比赛列表"""
    url = f"{API_BASE}?poolCode={pool_code}&channel=c&date={date_str}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    data = resp.json()
    if not data.get("success"):
        print(f"  API 错误: {data.get('errorMessage')}")
        return []

    matches = []
    for info in data["value"]["matchInfoList"]:
        for m in info.get("subMatchList", []):
            # 提取赔率数据
            odds_obj = m.get("hhad" if pool_code == "HHAD" else "had", {})
            if not odds_obj:
                continue

            matches.append({
                "jc_match_id": m["matchId"],
                "match_num": m["matchNumStr"],
                "league_name": m["leagueAllName"],
                "league_abb": m["leagueAbbName"],
                "home_team": m["homeTeamAllName"],
                "away_team": m["awayTeamAllName"],
                "home_abb": m["homeTeamAbbName"],
                "away_abb": m["awayTeamAbbName"],
                "match_date": m["matchDate"],
                "match_time": m["matchTime"],
                "pool_code": pool_code,
                "handicap_line": float(odds_obj.get("goalLineValue", 0) or 0),
                "odds_h": float(odds_obj.get("h", 0)),
                "odds_d": float(odds_obj.get("d", 0)),
                "odds_a": float(odds_obj.get("a", 0)),
                "update_time": f"{odds_obj.get('updateDate','')} {odds_obj.get('updateTime','')}",
            })
    return matches


async def match_and_update(matches: list, pool_code: str):
    """匹配本地比赛并更新让球线/赔率"""
    async with async_session() as db:
        updated = 0
        for jc in matches:
            # 1. 优先通过 jc_match_id 直接匹配
            result = await db.execute(
                select(Match).where(Match.jc_match_id == str(jc["jc_match_id"]))
            )
            matched = result.scalar_one_or_none()

            # 2. 按时间 + 队名匹配
            if not matched:
                try:
                    kt = datetime.strptime(f"{jc['match_date']} {jc['match_time']}", "%Y-%m-%d %H:%M:%S")
                except:
                    continue

                result = await db.execute(
                    select(Match).where(
                        Match.kickoff_time >= kt - timedelta(hours=12),
                        Match.kickoff_time <= kt + timedelta(hours=12),
                    )
                )
                for m in result.scalars().all():
                    home_team = await db.get(Team, m.home_team_id)
                    away_team = await db.get(Team, m.away_team_id)
                    if not home_team or not away_team:
                        continue

                    h_name = home_team.name_zh or home_team.name_en or ""
                    a_name = away_team.name_zh or away_team.name_en or ""

                    h_match = (jc["home_team"] in h_name or h_name in jc["home_team"] or
                              jc["home_abb"] in h_name or (h_name and h_name in jc["home_abb"]))
                    a_match = (jc["away_team"] in a_name or a_name in jc["away_team"] or
                              jc["away_abb"] in a_name or (a_name and a_name in jc["away_abb"]))

                    if h_match and a_match:
                        matched = m
                        break

            if not matched:
                continue

            # 更新 jc_match_id 和 match_num
            if not matched.jc_match_id:
                matched.jc_match_id = str(jc["jc_match_id"])
            if not matched.match_num:
                matched.match_num = jc["match_num"]

            # 更新让球线（仅 HHAD pool）
            if pool_code == "HHAD":
                matched.handicap_line = jc["handicap_line"]

            # 存储赔率快照（兼容 OddsSnapshot 字段）
            snapshot = OddsSnapshot(
                match_id=matched.id,
                bookmaker="竞彩官方",
                home_win=jc["odds_h"] if pool_code == "HAD" else None,
                draw=jc["odds_d"] if pool_code == "HAD" else None,
                away_win=jc["odds_a"] if pool_code == "HAD" else None,
                handicap_line=jc["handicap_line"] if pool_code == "HHAD" else None,
                handicap_home=jc["odds_h"] if pool_code == "HHAD" else None,
                handicap_away=jc["odds_a"] if pool_code == "HHAD" else None,
                snapshot_time=datetime.now(),
            )
            db.add(snapshot)
            updated += 1

        await db.commit()
        return updated


async def main():
    today = date.today()
    dates = [today.strftime("%Y-%m-%d"), (today + timedelta(days=1)).strftime("%Y-%m-%d")]

    total_updated = 0
    for dt in dates:
        print(f"\n{'='*50}")
        print(f"日期: {dt}")
        for pool in ["HHAD", "HAD"]:
            print(f"  拉取 {POOL_MAP[pool]}...")
            matches = fetch_jc(dt, pool)
            print(f"    获取 {len(matches)} 场比赛")

            if matches:
                n = await match_and_update(matches, pool)
                print(f"    匹配更新: {n} 场")
                total_updated += n

    print(f"\n总计更新: {total_updated} 场")

    # 统计可训练的让球样本
    async with async_session() as db:
        from sqlalchemy import func
        r = await db.execute(
            select(func.count()).select_from(Match).where(
                Match.handicap_line.isnot(None),
                Match.handicap_line != 0,
                Match.home_score.isnot(None),
            )
        )
        labeled = r.scalar()
        print(f"可训练让球样本（有让球线+比分）: {labeled} 场")


if __name__ == "__main__":
    asyncio.run(main())
