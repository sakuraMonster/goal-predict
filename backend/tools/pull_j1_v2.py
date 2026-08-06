"""反向推算日职联：从一场比赛找 season_id → 全量拉取"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from datetime import datetime, timedelta
from app.db.database import async_session
from app.db.models import Match, Team, League
from app.collector.sportmonks.client import SportMonksClient
from sqlalchemy import select, update, func

J1_SM_ID = 968
J1_LOCAL_ID = 7

async def main():
    c = SportMonksClient()
    try:
        # 1. 找一场日职联比赛
        fixtures = await c.get_fixtures_by_date("2025-03-01", "participants;scores;league;season")
        j1 = [f for f in fixtures if f.get("league_id") == J1_SM_ID]
        
        if not j1:
            # 试试其他日期
            for dt in ["2025-04-12", "2025-05-03", "2025-08-16"]:
                fixtures = await c.get_fixtures_by_date(dt, "participants;scores;league;season")
                j1 = [f for f in fixtures if f.get("league_id") == J1_SM_ID]
                if j1: break
        
        if not j1:
            print("未找到日职联比赛")
            return
        
        f = j1[0]
        print(f"比赛: {f['id']} - {f.get('name')}")
        print(f"  league_id: {f.get('league_id')}")
        print(f"  season_id: {f.get('season_id')}")
        
        season_data = f.get("season", {}) if isinstance(f.get("season"), dict) else {}
        season_id = f.get("season_id") or season_data.get("id")
        print(f"  season详情: {season_data}")
        
        # 2. 用这个 season_id 拉所有 fixtures
        if not season_id:
            print("无法获取 season_id")
            return
        
        print(f"\n使用 season_id={season_id} 拉取全部日职联...")
        
        # 分页拉取 by-season
        all_j1 = []
        page = 1
        while True:
            try:
                data = await c._get("/fixtures", params={
                    "filter[season_id]": season_id,
                    "include": "participants;scores",
                    "page": page,
                })
                batch = data.get("data", [])
                if not batch: break
                all_j1.extend(batch)
                
                pag = data.get("pagination", {})
                if page >= pag.get("last_page", page): break
                page += 1
                print(f"  第{page}页 ({len(all_j1)} 场)")
            except Exception as e:
                print(f"  page {page}: {e}")
                break
        
        print(f"  总计 {len(all_j1)} 场日职联比赛")
        
        # 3. 入库
        async with async_session() as db:
            await db.execute(update(League).where(League.id==J1_LOCAL_ID).values(sportmonks_id=J1_SM_ID))
            await db.commit()
            
            existing_teams = await db.execute(select(Team))
            tmap = {t.sportmonks_id: t for t in existing_teams.scalars().all() if t.sportmonks_id}
            
            added, fixed = 0, 0
            for f in all_j1:
                fid = f["id"]
                ex = await db.execute(select(Match).where(Match.sportmonks_fixture_id==fid))
                m = ex.scalar_one_or_none()
                if m:
                    if m.league_id != J1_LOCAL_ID:
                        m.league_id = J1_LOCAL_ID
                        fixed += 1
                    continue
                
                pp = f.get("participants", [])
                hp = next((p for p in pp if (p.get("meta",{})or{}).get("location")=="home"), None)
                ap = next((p for p in pp if (p.get("meta",{})or{}).get("location")=="away"), None)
                if not hp or not ap: continue
                hid, aid = hp["id"], ap["id"]
                
                for pid, pn in [(hid, hp.get("name","?")), (aid, ap.get("name","?"))]:
                    if pid not in tmap:
                        t = Team(sportmonks_id=pid, league_id=J1_LOCAL_ID, name_zh=pn, name_en=pn, short_en=(pn or "?")[:3])
                        db.add(t); tmap[pid] = t
                
                hs, asc = None, None
                for s in f.get("scores", []):
                    if s.get("description") in ("CURRENT","FT"):
                        g = (s.get("score") or {}).get("goals")
                        if g is not None:
                            if s.get("participant_id")==hid: hs=int(g)
                            else: asc=int(g)
                
                ko = f.get("starting_at")
                if ko: ko = datetime.fromisoformat(ko.replace("Z","+00:00")).replace(tzinfo=None)
                else: continue
                
                db.add(Match(sportmonks_fixture_id=fid, league_id=J1_LOCAL_ID,
                    home_team_id=tmap[hid].id, away_team_id=tmap[aid].id,
                    home_team_name=hp.get("name","?"), away_team_name=ap.get("name","?"),
                    kickoff_time=ko, home_score=hs, away_score=asc,
                    status="finished" if hs is not None else "scheduled"))
                added += 1
                
                if added % 200 == 0: await db.commit()
            
            await db.commit()
            
            # 统计
            j1_count = await db.execute(select(func.count()).select_from(Match).where(Match.league_id==J1_LOCAL_ID))
            j1_scored = await db.execute(select(func.count()).select_from(Match).where(Match.league_id==J1_LOCAL_ID, Match.home_score.isnot(None)))
            print(f"\n日职联总计: {j1_count.scalar()} 场, 有比分: {j1_scored.scalar()} 场")
            print(f"新增: {added}, 修正league: {fixed}")
        
        await c.close()
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())
