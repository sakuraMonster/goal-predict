"""SM standings 实测：随机取 2 个活跃联赛，验证 get_standings_by_season 可用
输出：_out_sm_standings.txt
"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(override=True)
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import League
from app.collector.sportmonks.client import SportMonksClient

OUT = []
def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

async def main():
    sm = SportMonksClient()
    async with async_session() as db:
        leagues = (await db.execute(
            select(League).where(League.sportmonks_id.isnot(None), League.active == True).limit(6)
        )).scalars().all()
        log(f"活跃联赛: {[(l.id, l.name_zh, l.sportmonks_id) for l in leagues]}")
        for lg in leagues:
            try:
                d = await sm.get_league_by_id(lg.sportmonks_id)
                seasons = d.get("seasons") or []
                cur = next((s for s in seasons if s.get("is_current")), None)
                if not cur:
                    log(f"  {lg.name_zh}: 无当前赛季, seasons={[(s.get('name'), s.get('is_current')) for s in seasons[:3]]}")
                    continue
                st = await sm.get_standings_by_season(cur["id"])
                log(f"  {lg.name_zh}: season={cur.get('name')}(id={cur['id']}) standings {len(st)} 条")
                if st:
                    log(f"    样例: {json.dumps(st[0], ensure_ascii=False, default=str)[:400]}")
            except Exception as ex:
                log(f"  {lg.name_zh}: 异常 {type(ex).__name__}: {str(ex)[:200]}")
    await sm.close()

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_sm_standings.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[saved to _out_sm_standings.txt]")

asyncio.run(main())
