"""SM 实测搜索 08-15 未映射球队，确认英文名 -> SM id"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.collector.sportmonks.client import SportMonksClient

# (本地球队id, 中文名, 竞彩EN, 搜索关键词列表)
TEAMS = [
    ("1723", "基尔", "HKL", ["Holstein Kiel", "Kiel"]),
    ("1724", "不伦瑞克", "BRC", ["Eintracht Braunschweig", "Braunschweig"]),
    ("1725", "新未来SC", "NEO", ["NEOM"]),
    ("1726", "迈季迈阿宽广", "AFH", ["Al Fayha", "Al-Fayha", "Fayha"]),
    ("1727", "达曼协定", "AEQ", ["Al Ettifaq", "Ettifaq"]),
    ("1728", "利雅得", "RYH", ["Al Riyadh", "Riyadh"]),
    ("1729", "利雅得新月", "", ["Al Hilal", "Hilal"]),
    ("1730", "哈马赫费萨利", "", ["Al Faisaly", "Faisaly", "Al-Fayha"]),
    ("1731", "瓦尔韦克", "RKC", ["RKC Waalwijk", "Waalwijk"]),
    ("1732", "多德勒支", "DDT", ["Dordrecht"]),
    ("1733", "赫拉克勒斯", "HER", ["Heracles Almelo", "Heracles"]),
    ("1734", "登博思", "DBH", ["Den Bosch", "FC Den Bosch"]),
    ("1735", "罗德兹", "ROD", ["Rodez"]),
    ("1736", "兰斯", "REM", ["Reims"]),
    ("1737", "敦刻尔克", "DKQ", ["Dunkerque"]),
    ("1738", "圣埃蒂安", "SET", ["Saint-Etienne", "Saint Etienne"]),
    ("1739", "克莱蒙", "CLE", ["Clermont"]),
    ("1740", "伍尔弗汉普顿", "WLV", ["Wolverhampton"]),
    ("1741", "布莱克本", "BLA", ["Blackburn"]),
]


async def main():
    sm = SportMonksClient()
    for tid, zh, en, terms in TEAMS:
        print(f"\n### {tid} {zh} (EN={en})", flush=True)
        for term in terms:
            try:
                results = await sm.search_teams(term)
                if not results:
                    continue
                print(f"  搜索 '{term}':", flush=True)
                for r in results[:4]:
                    print(f"    id={r.get('id')} name={r.get('name')} "
                          f"short={r.get('short_code')} country_id={r.get('country_id')} "
                          f"country={r.get('country')}", flush=True)
                break  # 第一个有结果的词就够了
            except Exception as e:
                print(f"  搜索 '{term}' 失败: {e}", flush=True)
    await sm.close()


asyncio.run(main())
