"""批量查询 SportMonks 球队"""
import asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.collector.sportmonks.client import SportMonksClient

async def main():
    sm = SportMonksClient()
    # 竞彩网中文名 → SportMonks 搜索词
    searches = {
        "波兹南": ["Lech Poznan", "Lech"],
        "奥胡斯": ["Aarhus", "AGF"],
        "阿拉木图": ["Kairat", "Kairat Almaty"],
        "奥莫尼亚": ["Omonia", "Omonia Nicosia"],
        "哈茨": ["Hearts", "Heart of Midlothian"],
        "格风暴": ["Sturm Graz", "Sturm"],
        "库奥皮奥": ["KuPS", "Kuopion"],
        "萨巴赫": ["Sabah", "Sabah FK"],
        "索尔纳": ["AIK", "AIK Solna"],
        "腓特烈": ["Fredrikstad", "Fredrikstad FK"],
    }
    
    for zh, terms in searches.items():
        print(f"\n{zh}:")
        best = None
        for term in terms:
            try:
                results = await sm.search_teams(term)
                for t in results[:3]:
                    tid = t.get("id")
                    tname = t.get("name", "")
                    short = t.get("short_code", "")
                    country = t.get("country", {}).get("name", "") if isinstance(t.get("country"), dict) else ""
                    print(f"  {term:20s} → id={tid:6d}  {tname:30s}  [{short}]  {country}")
                    if not best and tid:
                        best = {"id": tid, "name": tname, "short": short}
            except Exception as e:
                print(f"  {term:20s} → 查询失败: {e}")
        
        if best:
            print(f"  >>> 推荐: id={best['id']}  {best['name']}  [{best['short']}]")

asyncio.run(main())
