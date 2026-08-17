"""SM 实测 2：HIFK(912) vs Tampere United(8998) 球队维度排查
1) get_head_to_head 看历史交锋是否存在
2) teams/{id} 确认 912/8998 是否为当前芬超球队（含 season）
3) fixtures/between 08-14~08-20 看是否有这场（不依赖 /fixtures/date）
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
    try:
        h2h = await sm.get_head_to_head(912, 8998)
        log(f"[H2H] 912 vs 8998: {len(h2h)} 条")
        for h in h2h[:10]:
            pids = [p.get("id") for p in (h.get("participants") or [])]
            lg = (h.get("league") or {}).get("name", "")
            log(f"   fx={h.get('id')} {h.get('starting_at')} [{lg}] participants={pids}")
    except Exception as e:
        log(f"[H2H] 异常 {type(e).__name__}: {e}")

    for tid in (912, 8998):
        try:
            t = await sm.get_team_by_id(tid)
            log(f"[TEAM {tid}] name={t.get('name')} short={t.get('short_code')} ")
            for s in (t.get("seasons") or [])[:6]:
                log(f"   season id={s.get('id')} name={s.get('name')} league={s.get('league', {}).get('name') if isinstance(s.get('league'), dict) else s.get('league')}")
        except Exception as e:
            log(f"[TEAM {tid}] 异常 {type(e).__name__}: {e}")

    try:
        fixtures = await sm.get_fixtures_between("2026-08-14", "2026-08-20", includes="participants")
        log(f"[BETWEEN] 08-14~08-20: {len(fixtures)} 场")
        for fx in fixtures:
            pids = [p.get("id") for p in (fx.get("participants") or []) if isinstance(p, dict)]
            if 912 in pids or 8998 in pids:
                names = []
                for p in (fx.get("participants") or []):
                    if isinstance(p, dict):
                        names.append(f"{p.get('name')}({p.get('id')})")
                lg = (fx.get("league") or {}).get("name", "")
                log(f"   命中: fx={fx.get('id')} {fx.get('starting_at')} [{lg}] {' vs '.join(names)}")
    except Exception as e:
        log(f"[BETWEEN] 异常 {type(e).__name__}: {e}")
    await sm.close()

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_hifk_probe2.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\n[saved to _out_hifk_probe2.txt]")


asyncio.run(main())
