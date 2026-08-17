"""拉取竞彩网 API，获取 08-15 比赛日 19 支未映射球队的英文缩写"""
import httpx

API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.sporttery.cn/",
}

# 未映射球队 id -> 中文名
TARGETS = {
    "1723": "基尔", "1724": "不伦瑞克", "1725": "新未来SC", "1726": "迈季迈阿宽广",
    "1727": "达曼协定", "1728": "利雅得", "1729": "利雅得新月", "1730": "哈马赫费萨利",
    "1731": "瓦尔韦克", "1732": "多德勒支", "1733": "赫拉克勒斯", "1734": "登博思",
    "1735": "罗德兹", "1736": "兰斯", "1737": "敦刻尔克", "1738": "圣埃蒂安",
    "1739": "克莱蒙", "1740": "伍尔弗汉普顿", "1741": "布莱克本",
}

for date_str in ("2026-08-14", "2026-08-15"):
    url = f"{API}?poolCode=had&channel=c&date={date_str}"
    try:
        r = httpx.get(url, headers=HEADERS, timeout=15)
        data = r.json()
        if not data.get("success"):
            print(f"[{date_str}] API 错误: {data.get('errorMessage')}")
            continue
        print(f"\n===== {date_str} 竞彩比赛 =====")
        for info in data["value"]["matchInfoList"]:
            for m in info.get("subMatchList", []):
                home = m.get("homeTeamAllName", "")
                away = m.get("awayTeamAllName", "")
                # 只看包含目标球队的比赛
                hit = any(t in home or t in away for t in TARGETS.values())
                print(f"  {m.get('matchNumStr')} [{m.get('leagueAllName')}] "
                      f"{home}(EN={m.get('homeTeamAbbEnName')}) vs "
                      f"{away}(EN={m.get('awayTeamAbbEnName')}) "
                      f"{m.get('matchDate')} {m.get('matchTime')}")
    except Exception as e:
        print(f"[{date_str}] 失败: {e}")
