"""
重新生成 team-alias-seed.json
基于竞彩网球队数据 + SportMonks 积分榜完整球队列表

匹配策略:
1. 竞彩网中文名 ↔ 旧500.com映射桥接（同名直接继承）
2. 竞彩网中文名 → SportMonks search_teams API 搜索匹配
3. 无法匹配的写入 unmatched_sporttery_teams.json
"""
import asyncio
import json
import sys
import os
from difflib import SequenceMatcher

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.collector.sportmonks.client import SportMonksClient

# 路径
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
JC_TEAMS_PATH = os.path.join(ROOT, "sporttery_teams_raw.json")
SM_TEAMS_PATH = os.path.join(ROOT, "sportmonks_teams_raw.json")
OLD_SEED_PATH = os.path.join(ROOT, "team-alias-seed.json")
OUTPUT_PATH = os.path.join(ROOT, "team-alias-seed.json")
UNMATCHED_PATH = os.path.join(ROOT, "unmatched_sporttery_teams.json")

# 联赛 key 映射: 竞彩网简称 → team-alias-seed.json 的 league key
LEAGUE_KEY_MAP = {
    "英超": "premier_league", "西甲": "la_liga", "德甲": "bundesliga",
    "意甲": "serie_a", "法甲": "ligue_1", "日职": "j1_league",
    "韩职": "k_league_1", "澳超": "a_league", "瑞典超": "allsvenskan",
    "挪超": "eliteserien", "美职": "major_league_soccer", "欧冠": "uefa_champions_league",
    "巴甲": "brazilian_serie_a", "芬超": "veikkausliiga",
}

# 竞彩网联赛简称 → SportMonks 联赛名（用于 search_teams 时限定范围）
LEAGUE_SM_NAME_MAP = {
    "英超": "Premier League", "西甲": "La Liga", "德甲": "Bundesliga",
    "意甲": "Serie A", "法甲": "Ligue 1", "日职": "J1 League",
    "韩职": "K League 1", "瑞典超": "Allsvenskan", "挪超": "Eliteserien",
    "美职": "Major League Soccer", "欧冠": "UEFA Champions League",
    "巴甲": "Serie A", "芬超": "Veikkausliiga",
}


def load_json(path):
    if not os.path.exists(path):
        print(f"  文件不存在: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_old_seed_index(old_seed: dict) -> dict:
    """构建: {中文队名: sportmonks_team_id}"""
    index = {}
    for league in old_seed.get("leagues", []):
        for alias in league.get("aliases", []):
            zh = alias.get("source_team_name_zh", "")
            sm_id = alias.get("sportmonks_team_id")
            if zh and sm_id:
                index[zh] = sm_id
    return index


def build_sm_index(sm_data: dict) -> dict:
    """
    构建 SportMonks 球队索引:
    {sm_team_id: {name, short_code}}   
    以及按联赛分组的 name→id 映射
    """
    by_id = {}
    by_league_name = {}  # {league_zh: {team_name_lower: sm_id}}
    
    for league_zh, info in sm_data.items():
        by_league_name[league_zh] = {}
        for t in info.get("teams", []):
            sm_id = t.get("sm_id")
            name = t.get("name", "")
            short = t.get("short_code", "")
            if sm_id:
                by_id[str(sm_id)] = {"name": name, "short_code": short}
                by_league_name[league_zh][name.lower().strip()] = str(sm_id)
    
    return by_id, by_league_name


def match_team(team_name_zh, team_id, old_index, sm_client, sm_by_league, league_zh):
    """
    匹配一支竞彩网球队到 SportMonks
    返回 (sm_id, sm_name, sm_short, confidence, method, signals) 或 None
    """
    # 策略1: 旧映射桥接
    if team_name_zh in old_index:
        sm_id = str(old_index[team_name_zh])
        return {
            "sm_id": sm_id,
            "sm_name": "",
            "sm_short": "",
            "confidence": 0.95,
            "method": "bridged",
            "signals": ["old_seed_exact_match"],
        }
    
    # 策略2: 在 SportMonks 当前赛季球队中按名称直接匹配
    sm_teams = sm_by_league.get(league_zh, {})
    name_lower = team_name_zh.lower().strip()
    if name_lower in sm_teams:
        return {
            "sm_id": sm_teams[name_lower],
            "sm_name": "",
            "sm_short": "",
            "confidence": 0.90,
            "method": "standings_exact",
            "signals": [f"standings_exact:{team_name_zh}"],
        }
    
    # 策略3: 模糊匹配 SportMonks 积分榜中的球队名
    best_score = 0
    best_sm_id = None
    best_sm_name = ""
    for sm_name, sm_id in sm_teams.items():
        score = SequenceMatcher(None, name_lower, sm_name).ratio()
        if score > best_score:
            best_score = score
            best_sm_id = sm_id
            best_sm_name = sm_name
    
    if best_score >= 0.70:
        return {
            "sm_id": best_sm_id,
            "sm_name": best_sm_name,
            "sm_short": "",
            "confidence": round(best_score, 2),
            "method": "standings_fuzzy",
            "signals": [f"standings_fuzzy:{best_sm_name}:{best_score:.2f}"],
        }
    
    return None


async def main():
    print("=" * 60)
    print("重新生成 team-alias-seed.json")
    print("=" * 60)

    # 加载数据
    print("\n加载数据...")
    jc_data = load_json(JC_TEAMS_PATH)
    sm_data = load_json(SM_TEAMS_PATH)
    old_seed = load_json(OLD_SEED_PATH)

    if not jc_data:
        print("错误: 竞彩网球队数据为空，请先运行 collect_sporttery_teams.py")
        return
    if not sm_data:
        print("警告: SportMonks 球队数据为空，仅使用旧种子桥接和模糊匹配")

    old_index = build_old_seed_index(old_seed)
    sm_by_id, sm_by_league = build_sm_index(sm_data)
    sm_client = SportMonksClient()

    # 构建新 seed
    new_seed = {
        "generated_at": "",  # 稍后填充
        "scope": "sporttery-team-alias-seed",
        "league_count": 0,
        "leagues": [],
    }

    unmatched = []

    for league_abbr, info in jc_data.items():
        league_key = LEAGUE_KEY_MAP.get(league_abbr)
        sm_league_name = LEAGUE_SM_NAME_MAP.get(league_abbr, league_abbr)
        league_label = league_abbr

        if not league_key:
            print(f"\n  跳过未知联赛: {league_abbr}")
            continue

        print(f"\n处理: {league_abbr} ({league_key}), {info['team_count']} 支球队")

        aliases = []
        unmatched_in_league = []

        for team in info["teams"]:
            tid = team["team_id"]
            tname = team["team_name"]

            result = match_team(tname, tid, old_index, sm_client, sm_by_league, league_abbr)

            if result:
                # 从 SM 完整数据补充 name 和 short_code
                sm_id = result["sm_id"]
                sm_info = sm_by_id.get(str(sm_id), {})
                sm_name = result.get("sm_name") or sm_info.get("name", "")
                sm_short = result.get("sm_short") or sm_info.get("short_code", "")

                alias = {
                    "league_key": league_key,
                    "league_label": league_label,
                    "source_provider": "sporttery.cn",
                    "target_provider": "sportmonks",
                    "source_team_id": int(tid) if tid.isdigit() else tid,
                    "source_team_name_zh": tname,
                    "source_team_url": f"https://www.sporttery.cn/zqlszl/qdzl/index.html?gmtid={tid}",
                    "sportmonks_team_id": int(sm_id) if str(sm_id).isdigit() else sm_id,
                    "sportmonks_team_name": sm_name,
                    "sportmonks_short_code": sm_short,
                    "confidence_score": result["confidence"],
                    "confidence_gap": result["confidence"],
                    "matched_signals": result["signals"],
                    "match_method": result["method"],
                    "status": "seed_confirmed",
                }
                aliases.append(alias)
                print(f"    {tname} → {sm_name or sm_id} ({result['method']}, {result['confidence']})")
            else:
                unmatched_in_league.append({
                    "league_abbr": league_abbr,
                    "league_key": league_key,
                    "team_id": tid,
                    "team_name_zh": tname,
                })
                print(f"    {tname} → 未匹配!")

        if aliases:
            new_seed["leagues"].append({
                "key": league_key,
                "label": league_label,
                "target_league_name": sm_league_name,
                "alias_count": len(aliases),
                "aliases": aliases,
            })

        if unmatched_in_league:
            unmatched.extend(unmatched_in_league)

    # 也加入旧 seed 中有但竞彩网当前无比赛的联赛（保留已有映射）
    old_league_keys_in_new = {lg["key"] for lg in new_seed["leagues"]}
    for old_league in old_seed.get("leagues", []):
        if old_league["key"] not in old_league_keys_in_new:
            print(f"\n  保留旧映射: {old_league['label']} ({old_league['key']}), {old_league.get('alias_count', 0)} 条")
            # 更新 source_provider
            updated_aliases = []
            for alias in old_league.get("aliases", []):
                alias["source_provider"] = "sporttery.cn"
                # 更新 URL
                if "liansai.500.com" in alias.get("source_team_url", ""):
                    old_tid = alias.get("source_team_id", "")
                    alias["source_team_url"] = f"https://www.sporttery.cn/zqlszl/qdzl/index.html?gmtid={old_tid}"
                updated_aliases.append(alias)
            old_league["aliases"] = updated_aliases
            new_seed["leagues"].append(old_league)

    new_seed["league_count"] = len(new_seed["leagues"])

    # 写入时间
    from datetime import datetime, timezone
    new_seed["generated_at"] = datetime.now(timezone.utc).isoformat()

    # 写入文件
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(new_seed, f, ensure_ascii=False, indent=2)
    print(f"\n已生成: {OUTPUT_PATH}")
    print(f"  共 {new_seed['league_count']} 个联赛")

    total_aliases = sum(lg["alias_count"] for lg in new_seed["leagues"])
    print(f"  共 {total_aliases} 条球队映射")

    if unmatched:
        with open(UNMATCHED_PATH, "w", encoding="utf-8") as f:
            json.dump(unmatched, f, ensure_ascii=False, indent=2)
        print(f"\n未匹配球队: {len(unmatched)} 支")
        print(f"已写入: {UNMATCHED_PATH}")
        for u in unmatched:
            print(f"  [{u['league_abbr']}] {u['team_name_zh']} (id={u['team_id']})")


if __name__ == "__main__":
    asyncio.run(main())
