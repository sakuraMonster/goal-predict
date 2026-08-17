"""SM 实测 3：芬超 08-17 当天 fixture + HIFK 912 赛季归属
1) team 912 include=seasons 看当前所属联赛/赛季
2) fixtures/between 08-14~08-20 中所有芬兰联赛比赛（league country=Finland）
3) 芬超所有球队下一轮赛程（通过 team 912 的 next fixtures）
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

    # 1) team 912 赛季归属
    try:
        t = await sm.get_team_by_id(912)
        log(f"[TEAM 912] name={t.get('name')} country={t.get('country', {}).get('name') if isinstance(t.get('country'), dict) else t.get('country')}")
        seasons = t.get("seasons") or []
        log(f"  seasons count={len(seasons)}")
        for s in seasons:
            lg = s.get("league")
            lg_name = lg.get("name") if isinstance(lg, dict) else lg
            lg_id = lg.get("id") if isinstance(lg, dict) else None
            log(f"   season={s.get('id')} {s.get('name')} league_id={lg_id} [{lg_name}]")
    except Exception as e:
        log(f"[TEAM 912] 异常 {type(e).__name__}: {e}")

    # 2) between 窗口内芬兰联赛比赛
    try:
        fixtures = await sm.get_fixtures_between("2026-08-14", "2026-08-20", includes="participants")
        fin = []
        for fx in fixtures:
            lg = fx.get("league") or {}
            lg_name = lg.get("name") or ""
            if "Veikkausliiga" in lg_name or "Ykkös" in lg_name or "Suomen" in lg_name:
                fin.append(fx)
        log(f"[BETWEEN-FIN] 08-14~08-20 芬兰联赛 {len(fin)} 场:")
        for fx in fin:
            lg = (fx.get("league") or {}).get("name", "")
            names = []
            for p in (fx.get("participants") or []):
                if isinstance(p, dict):
                    names.append(f"{p.get('name')}({p.get('id')})")
            log(f"   fx={fx.get('id')} {fx.get('starting_at')} [{lg}] {' vs '.join(names)}")
    except Exception as e:
        log(f"[BETWEEN-FIN] 异常 {type(e).__name__}: {e}")
    await sm.close()

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_hifk_probe3.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n[saved to _out_hifk_probe3.txt]")


asyncio.run(main())
