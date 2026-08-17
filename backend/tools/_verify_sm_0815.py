"""SM API 验证：已匹配 13 场 fixture 参与者 + 未匹配球队搜索
"""
import asyncio
import sys
sys.path.insert(0, ".")
from app.collector.sportmonks.client import SportMonksClient

# (match_id, fx, home_name, home_sm, away_name, away_sm)
MATCHED = [
    (15621, 19719030, "鹿岛鹿角", 3690, "名古屋鲸八", 8738),
    (15623, 19719028, "浦和红钻", 280, "广岛三箭", 14738),
    (15624, 19719024, "神户胜利船", 3867, "东京FC", 2988),
    (15625, 19648098, "首尔FC", 672, "大田市民", 5664),
    (15626, 19648100, "光州FC", 4370, "浦项制铁", 1506),
    (15628, 19714690, "威廉二世", 669, "奈梅亨", 494),
    (15629, 19736847, "阿尔维卡", 269225, "阿马多拉", 12152),
    (15630, 19714689, "乌德勒支", 750, "阿尔克马尔", 61),
    (15631, 19736848, "维塞乌", 8297, "圣克拉拉", 2628),
    (15632, 19732742, "阿拉维斯", 2975, "赫塔费", 106),
    (15634, 19714687, "福图纳锡塔德", 1459, "坎布尔", 1435),
    (15635, 19732734, "塞维利亚", 676, "巴列卡诺", 377),
    (15636, 19736841, "里奥阿维", 6377, "波尔图", 652),
]

SEARCHES = [
    "Excelsior Rotterdam",   # SBV精英
    "1. FC Heidenheim",      # 海登海姆
    "Blaublitz Akita",       # 秋田蓝色闪电
    "Kataller Toyama",       # 富山胜利
    "Hertha BSC",            # 柏林赫塔 验证 3317
    "Exeter City",           # 验证 185 到底是谁
]


async def main():
    sm = SportMonksClient()
    print("========== 已匹配 fixture 验证 ==========")
    for mid, fx, hz, hsm, az, asm in MATCHED:
        try:
            fx_data = await sm.get_fixture_by_id(fx, includes="participants")
        except Exception as e:
            print(f"  #{mid} fx={fx} 查询失败: {e}")
            continue
        parts = fx_data.get("participants", [])
        pids = {p.get("id") for p in parts if isinstance(p, dict)}
        pnames = {f"{p.get('id')}:{p.get('name')}({p.get('meta',{}).get('location','?')})" for p in parts if isinstance(p, dict)}
        ok_home = hsm in pids
        ok_away = asm in pids
        mark = "OK" if (ok_home and ok_away) else "** MISMATCH **"
        print(f"  #{mid} fx={fx} [{mark}] 期望 {hz}({hsm}) vs {az}({asm})")
        print(f"       SM participants: {sorted(pnames)}")

    print("\n========== 未匹配球队 SM 搜索 ==========")
    for q in SEARCHES:
        try:
            res = await sm.search_teams(q)
        except Exception as e:
            print(f"  search {q} 失败: {e}")
            continue
        print(f"  --- {q} ---")
        for t in res[:5]:
            print(f"      id={t.get('id')} name={t.get('name')} country={t.get('country')}")

    await sm.close()


asyncio.run(main())
