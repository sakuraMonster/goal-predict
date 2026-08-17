"""SM sidelined API 实测：伤停数据是否可拿（team/fixture 两个入口）+ 结构 + 本地映射
结论依据：SM v3 无独立 /injuries 端点，伤停走 sidelined include
输出：_out_sm_injuries.txt
"""
import asyncio, os, sys, json
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Team, Match, League
from app.collector.sportmonks.client import SportMonksClient

OUT = []
def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

async def main():
    sm = SportMonksClient()
    async with async_session() as db:
        # 挑 6 支有 sm_id 的活跃联赛球队
        rows = (await db.execute(
            select(Team).where(Team.sportmonks_id.isnot(None))
            .join(League, Team.league_id == League.id)
            .where(League.active == True)
            .limit(8)
        )).scalars().all()
        log(f"选中的本地球队: {[(t.id, t.name_zh, t.sportmonks_id) for t in rows]}")

        # ── 1) team 入口 sidelined ──
        log("\n===== 1) /teams/{id}?include=sidelined.player;sidelined.type =====")
        for t in rows[:5]:
            try:
                data = await sm.get_team_by_id(t.sportmonks_id, includes="sidelined.player;sidelined.type")
                d = data.get("data") or {}
                sd = d.get("sidelined") or []
                log(f"  {t.name_zh}(sm={t.sportmonks_id}): sidelined {len(sd)} 条")
                for e in sd[:5]:
                    p = e.get("player") or {}
                    ty = e.get("type") or {}
                    log(f"    player={p.get('common_name') or p.get('name')} type={ty.get('name')} "
                        f"category={e.get('category')} start={e.get('start_date')} end={e.get('end_date')} "
                        f"completed={e.get('completed')} games_missed={e.get('games_missed')}")
                if sd:
                    log(f"    字段样例: {list(sd[0].keys())}")
            except Exception as ex:
                log(f"  {t.name_zh}: 异常 {type(ex).__name__}: {ex}")
        await sm.close()

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_sm_injuries.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print("\n[saved to _out_sm_injuries.txt]")

asyncio.run(main())
