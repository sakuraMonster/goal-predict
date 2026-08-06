"""深度诊断：直接调SM API验证fixtures和H2H"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from app.collector.sportmonks.client import SportMonksClient


async def main():
    sm = SportMonksClient()

    # ====== 1. 测试 fixtures/between 的返回量 ======
    print("="*60)
    print("测试1: SM API fixtures/between 返回量")
    print("="*60)

    # 08-01 的匹配范围
    date_from = "2026-07-30"
    date_to = "2026-08-03"
    print(f"查询范围: {date_from} ~ {date_to}")

    fixtures = await sm.get_fixtures_between(date_from, date_to, includes="participants")
    print(f"返回 fixture 总数: {len(fixtures)}")

    # 统计各联赛的fixture数量
    league_counts = {}
    for fx in fixtures:
        league = fx.get("league", {}).get("name", "?") if isinstance(fx.get("league"), dict) else "?"
        league_id = fx.get("league_id", "?")
        league_counts[f"{league}(id={league_id})"] = league_counts.get(f"{league}(id={league_id})", 0) + 1

    print(f"\n各联赛 fixture 数量:")
    for league, count in sorted(league_counts.items(), key=lambda x: -x[1]):
        print(f"  {league}: {count}")

    # ====== 2. 检查缺失SM fixture的比赛是否在API返回中 ======
    print(f"\n{'='*60}")
    print("测试2: 搜索缺失球队对的SM fixture")
    print("="*60)

    # 无SM fixture的比赛球队对 (team SM IDs)
    missing_pairs = [
        # 08-01
        ("腓特烈斯塔 vs 桑纳菲", 562, 894),      # 15480 挪超
        ("赫根 vs 卡尔马", 3684, 11550),           # 15479 瑞典超
        ("TPS图尔库 vs 玛丽港", 2178, 2453),       # 15478 芬超
        ("拉赫蒂 vs 查路", 5697, 5700),            # 15481 芬超
        # 08-02
        ("斯达 vs 维京", 604, 271),                # 15482 挪超
        ("迈阿密国际 vs 哥伦布机员", 263948, 909),  # 15484 美职联
        ("温哥华白帽 vs 洛杉矶FC", 78, 4178),       # 15485 美职联
        ("AIK索尔纳 vs 厄尔格里特", 2825, 86),      # 15495 瑞典超
        ("莫尔德 vs 萨普斯堡", 290, 2601),          # 15497 挪超
    ]

    for name, t1, t2 in missing_pairs:
        found = []
        for fx in fixtures:
            pids = set()
            for p in fx.get("participants", []):
                if isinstance(p, dict):
                    pids.add(p.get("id"))
            if t1 in pids and t2 in pids:
                found.append(f"  → 找到! fixture_id={fx['id']} date={fx.get('starting_at','')[:10]} name={fx.get('name','')}")

        if found:
            print(f"  {name} (SM {t1} vs {t2}):")
            for f in found:
                print(f)
        else:
            print(f"  {name} (SM {t1} vs {t2}): ❌ 未在返回的 {len(fixtures)} 条 fixture 中找到")

    # ====== 3. 测试单日 API ======
    print(f"\n{'='*60}")
    print("测试3: 单日 fixtures API")
    print("="*60)
    for test_date in ["2026-08-01", "2026-08-02"]:
        day_fixtures = await sm.get_fixtures_by_date(test_date, includes="participants")
        print(f"  {test_date}: {len(day_fixtures)} fixtures")

    # ====== 4. 测试H2H API ======
    print(f"\n{'='*60}")
    print("测试4: H2H API 直接调用")
    print("="*60)
    h2h_pairs = [
        ("赫尔辛基火花 vs 库普斯", 912, 4323),
        ("AC奥卢 vs 坦佩雷山猫", 5696, 8998),
        ("AIK索尔纳 vs 厄尔格里特", 2825, 86),
        ("奥斯陆KFUM vs 克里斯蒂", 57, 869),
    ]
    for name, t1, t2 in h2h_pairs:
        data = await sm.get_head_to_head(t1, t2)
        if data:
            print(f"  {name}: ✓ {len(data)} 条")
            for h in data[:2]:
                print(f"    - {h.get('name','?')} ({h.get('starting_at','?')[:10]})")
        else:
            print(f"  {name}: ✗ 空（确实无历史交锋）")

    await sm.close()


if __name__ == "__main__":
    asyncio.run(main())
