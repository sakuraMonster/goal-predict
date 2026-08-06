import asyncio,os,sys
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv;load_dotenv()
from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import Match
from sqlalchemy import select

async def main():
    sm=SportMonksClient()
    
    async with async_session() as db:
        for mid in [15500, 15501]:
            r=await db.execute(select(Match).where(Match.id==mid))
            m=r.scalar_one_or_none()
            if not m: continue
            
            h=m.home_team.name_zh if m.home_team else m.home_team_name
            a=m.away_team.name_zh if m.away_team else m.away_team_name
            hsm=m.home_team.sportmonks_id if m.home_team else None
            asm=m.away_team.sportmonks_id if m.away_team else None
            print(f"Match ID={mid}: {h}({hsm}) vs {a}({asm}) kt={m.kickoff_time}")
            
            for date_str in ["2026-08-02", "2026-08-03", "2026-08-01", "2026-07-31"]:
                fixtures=await sm.get_fixtures_by_date(date_str,includes="participants")
                print(f"  {date_str}: {len(fixtures)} fixtures")
                
                for fx in fixtures:
                    pids={p.get("id") for p in fx.get("participants",[]) if isinstance(p,dict)}
                    if hsm in pids and asm in pids:
                        m.sportmonks_fixture_id=fx["id"]
                        print(f"  >>> FOUND! fixture_id={fx['id']} name={fx.get('name')} date={date_str}")
                        break
                    elif hsm in pids or asm in pids:
                        other=pids-{hsm,asm}
                        print(f"  partial: {hsm in pids}/{asm in pids} in fx={fx['id']} other={other} name={fx.get('name')}")
                else:
                    continue
                break
            else:
                print(f"  NOT FOUND in any date")
                
        await db.commit()
    await sm.close()

asyncio.run(main())
