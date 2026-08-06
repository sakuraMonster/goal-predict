import asyncio,os,sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv;load_dotenv()
from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import Match
from sqlalchemy import select

async def main():
    sm=SportMonksClient()
    # 北京时间 08-03 01:15~06:30 = UTC 08-02 17:15~22:30
    print("Query 08-02 (UTC)...")
    fixtures=await sm.get_fixtures_by_date("2026-08-02",includes="participants")
    print(f"Got {len(fixtures)} fixtures")
    
    async with async_session() as db:
        r=await db.execute(select(Match).where(Match.id.in_([15499,15500,15501])))
        for m in r.scalars().all():
            h=m.home_team.name_zh if m.home_team else m.home_team_name
            a=m.away_team.name_zh if m.away_team else m.away_team_name
            hsm=m.home_team.sportmonks_id if m.home_team else None
            asm=m.away_team.sportmonks_id if m.away_team else None
            found=None
            for fx in fixtures:
                pids={p.get("id") for p in fx.get("participants",[]) if isinstance(p,dict)}
                if hsm in pids and asm in pids:
                    found=fx
                    break
            if found:
                m.sportmonks_fixture_id=found["id"]
                print(f"OK ID={m.id} {h} vs {a} => fx={found['id']} {found.get('name')}")
            else:
                print(f"FAIL ID={m.id} {h}({hsm}) vs {a}({asm})")
        await db.commit()
    await sm.close()

asyncio.run(main())
