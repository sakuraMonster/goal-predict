"""SM API 实测：芬超 HIFK(912) vs Tampere United(8998) 是否有 fixture
判定：数据源缺失 vs 匹配逻辑问题
"""
import asyncio
import os
import sys
from dotenv import load_dotenv
load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collector.sportmonks.client import SportMonksClient


async def main():
    out = []
    def log(s=""):
        print(s)
        out.append(s)

    sm = SportMonksClient()
    # 竞彩开赛 2026-08-17 23:00 北京 → UTC 15:00；查询窗口 ±2 天
    for date in ["2026-08-15", "2026-08-16", "2026-08-17", "2026-08-18", "2026-08-19"]:
        try:
            fixtures = await sm.get_fixtures_by_date(date, includes="participants")
        except Exception as e:
            log(f"{date}: 异常 {type(e).__name__}: {e}")
            continue
        hits = []
        for fx in fixtures:
            pids = [p.get("id") for p in (fx.get("participants") or []) if isinstance(p, dict)]
            if 912 in pids or 8998 in pids:
                names = []
                for p in (fx.get("participants") or []):
                    if isinstance(p, dict):
                        names.append(f"{p.get('name')}(id={p.get('id')},loc={(p.get('meta') or {}).get('location')})")
                lg = (fx.get("league") or {}).get("name", "")
                hits.append(f"  fx={fx.get('id')} {fx.get('starting_at')} [{lg}] {' vs '.join(names)}")
        if hits:
            log(f"{date}: {len(fixtures)} 场，命中 {len(hits)} 场:")
            log("\n".join(hits))
        else:
            log(f"{date}: {len(fixtures)} 场，无 HIFK/Tampere 相关")
    await sm.close()

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_hifk_probe.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n[saved to _out_hifk_probe.txt]")


asyncio.run(main())
