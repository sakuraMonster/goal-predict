"""SM 实测 4：确认 SM 数据覆盖情况
1) search_teams 'HIFK' / 'Tampere United' 看有哪些实体
2) between 08-14~08-20 的 248 场按 league 分组，确认有无芬兰联赛
3) 芬超实际 fixture：尝试查询 Veikkausliiga 联赛 id
"""
import asyncio
import os
import sys
from collections import Counter
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

    for q in ["HIFK", "Helsinki IFK", "Tampere United", "Tampere Ilves"]:
        try:
            res = await sm.search_teams(q)
            log(f"[SEARCH {q}] {len(res)} 条:")
            for t in res[:6]:
                log(f"   id={t.get('id')} name={t.get('name')} country={t.get('country', {}).get('name') if isinstance(t.get('country'), dict) else t.get('country')}")
        except Exception as e:
            log(f"[SEARCH {q}] 异常 {type(e).__name__}: {e}")

    try:
        fixtures = await sm.get_fixtures_between("2026-08-14", "2026-08-20", includes="participants")
        league_counter = Counter()
        for fx in fixtures:
            lg = (fx.get("league") or {}).get("name", "未知")
            league_counter[lg] += 1
        log(f"[BETWEEN] 248 场按联赛: {len(league_counter)} 个联赛")
        for lg, cnt in league_counter.most_common(30):
            log(f"   {lg}: {cnt}")
    except Exception as e:
        log(f"[BETWEEN] 异常 {type(e).__name__}: {e}")
    await sm.close()

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_hifk_probe4.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n[saved to _out_hifk_probe4.txt]")


asyncio.run(main())
