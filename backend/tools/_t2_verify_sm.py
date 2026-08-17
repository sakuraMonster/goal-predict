"""t2 验证：葡超球队 sportmonks_id 映射核查
1. get_team_by_id 确认当前 sm_id 实际是什么球队
2. search_teams 搜索正确英文名
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.collector.sportmonks.client import SportMonksClient

# (team_id, 当前sm_id, 中文名, 期望英文名搜索词)
CURRENT = [
    (1299, 1498, "波尔图", ["Porto"]),
    (486, 652, "Porto(真)", ["Porto"]),
    (1678, 1822, "阿尔维卡", ["AVS", "AVS Futebol"]),
    (208, 292, "卡萨皮亚", ["Casa Pia"]),
    (1646, 255, "马里迪莫", ["Maritimo"]),
    (1662, 500, "阿马多拉", ["Estrela Amadora"]),
    (1680, 172, "里奥阿维", ["Rio Ave"]),
    (1651, 230547, "阿罗卡", ["Arouca"]),
    (785, 828, "Nacional", ["Nacional Madeira", "CD Nacional"]),
    (388, 830, "吉马良斯", ["Vitoria Guimaraes", "Vitoria SC"]),
    (389, 58, "里斯本竞技", ["Sporting Lisbon", "Sporting CP"]),
    (401, 1085, "Moreirense", ["Moreirense"]),
    (408, 884, "Sporting Braga", ["Braga"]),
    (422, 2628, "Santa Clara", ["Santa Clara"]),
]


async def main():
    sm = SportMonksClient()
    print("== 当前 sm_id 身份验证 ==")
    for tid, smid, zh, terms in CURRENT:
        try:
            data = await sm.get_team_by_id(smid)
            name = data.get("name", "?")
            short = data.get("short_code", "")
            country = data.get("country", {})
            cname = country.get("name", "") if isinstance(country, dict) else ""
            print(f"  id={tid} {zh}: sm={smid} -> {name} [{short}] {cname}")
        except Exception as e:
            print(f"  id={tid} {zh}: sm={smid} -> 查询失败 {e}")

    print("\n== 正确英文名搜索 ==")
    seen = set()
    for tid, smid, zh, terms in CURRENT:
        if zh in seen:
            continue
        seen.add(zh)
        for term in terms[:1]:
            try:
                results = await sm.search_teams(term)
                for t in results[:4]:
                    tid2 = t.get("id")
                    tname = t.get("name", "")
                    short = t.get("short_code", "")
                    country = t.get("country", {})
                    cname = country.get("name", "") if isinstance(country, dict) else ""
                    print(f"  {zh:12s} '{term}' -> id={tid2:7d} {tname:30s} [{short}] {cname}")
            except Exception as e:
                print(f"  {zh:12s} '{term}' -> 失败 {e}")

    await sm.close()


asyncio.run(main())
