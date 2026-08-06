"""专门拉取日职联 + 重训练"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

import numpy as np, pandas as pd, joblib
from datetime import datetime, timedelta
from app.db.database import async_session
from app.db.models import Match, Team, League, TeamSeasonStats, HeadToHead, Prediction
from app.collector.sportmonks.client import SportMonksClient
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB
from sqlalchemy import select, delete, update

J1_SM_ID = 968
J1_SEASON = 24946  # 2025
J1_LOCAL_ID = 7
MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)


async def main():
    client = SportMonksClient()
    try:
        async with async_session() as db:
            # 1. 更新联赛
            await db.execute(update(League).where(League.id==J1_LOCAL_ID).values(sportmonks_id=J1_SM_ID))
            await db.commit()
            print("日职联 sportmonks_id → 968")

            # 2. 加载已有球队
            existing = await db.execute(select(Team))
            tmap = {t.sportmonks_id: t for t in existing.scalars().all() if t.sportmonks_id}

            # 3. 按天拉取 + 修正 league_id
            print("拉取日职联 2025 赛季比赛（1月-12月）...")
            start, end = datetime(2025, 1, 1), datetime(2026, 1, 1)
            cur, added, fixed = start, 0, 0
            
            while cur < end:
                day_str = cur.strftime("%Y-%m-%d")
                try:
                    fixtures = await client.get_fixtures_by_date(day_str, "participants;scores")
                except Exception as e:
                    print(f"  {day_str}: 跳过 ({type(e).__name__})")
                    cur += timedelta(days=1)
                    continue

                for f in fixtures:
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
                    if ko:
                        ko = datetime.fromisoformat(ko.replace("Z","+00:00")).replace(tzinfo=None)
                    else: continue

                    db.add(Match(sportmonks_fixture_id=fid, league_id=J1_LOCAL_ID,
                        home_team_id=tmap[hid].id, away_team_id=tmap[aid].id,
                        home_team_name=hp.get("name","?"), away_team_name=ap.get("name","?"),
                        kickoff_time=ko, home_score=hs, away_score=asc,
                        status="finished" if hs is not None else "scheduled"))
                    added += 1

                if added > 0 and added % 100 == 0:
                    await db.commit()
                cur += timedelta(days=1)
                if cur.day in (1, 15) and cur.month % 3 == 0:
                    print(f"  {cur.strftime('%Y-%m')}: 新增{added} 修正{fixed}")

            await db.commit()
            print(f"完成: 新增 {added}, 修正league {fixed}")

            # 4. 统计日职联
            j1_matches = await db.execute(
                select(Match).where(Match.league_id==J1_LOCAL_ID, Match.home_score.isnot(None)))
            j1_scored = len(list(j1_matches.scalars().all()))
            print(f"日职联有比分: {j1_scored} 场")

            # 5. 重训练（全量）
            print("\n重训练...")
            await db.execute(delete(TeamSeasonStats))
            await db.execute(delete(HeadToHead))
            
            all_m = await db.execute(
                select(Match).where(Match.home_score.isnot(None), Match.home_team_id.isnot(None), Match.away_team_id.isnot(None)).order_by(Match.kickoff_time))
            matches = list(all_m.scalars().all())
            
            from collections import Counter
            lc = Counter(m.league_id for m in matches)
            print(f"总样本: {len(matches)} 场")
            for lid in sorted(lc): print(f"  league_id={lid}: {lc[lid]}")

            # stats
            sdict = {}
            for m in matches:
                sea = str(m.kickoff_time.year)
                for tid, ih, gf, ga in [(m.home_team_id,True,m.home_score,m.away_score),(m.away_team_id,False,m.away_score,m.home_score)]:
                    k = (tid, sea)
                    if k not in sdict:
                        sdict[k] = {"team_id":tid,"season":sea,"league_id":m.league_id,
                            "p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,"hw":0,"hd":0,"hl":0,"aw":0,"ad":0,"al":0,"cs":0,"fs":0,"r":[]}
                    s=sdict[k]; s["p"]+=1; s["gf"]+=gf; s["ga"]+=ga
                    if gf>ga: s["w"]+=1; s["r"].append("W")
                    elif gf==ga: s["d"]+=1; s["r"].append("D")
                    else: s["l"]+=1; s["r"].append("L")
                    if ih:
                        if gf>ga: s["hw"]+=1
                        elif gf==ga: s["hd"]+=1
                        else: s["hl"]+=1
                    else:
                        if gf>ga: s["aw"]+=1
                        elif gf==ga: s["ad"]+=1
                        else: s["al"]+=1
                    if ga==0: s["cs"]+=1
                    if gf==0: s["fs"]+=1
            
            for (tid,sea),s in sdict.items():
                db.add(TeamSeasonStats(team_id=tid, season=sea, league_id=s["league_id"],
                    played=s["p"],wins=s["w"],draws=s["d"],losses=s["l"],
                    goals_for=s["gf"],goals_against=s["ga"],
                    home_wins=s["hw"],home_draws=s["hd"],home_losses=s["hl"],
                    away_wins=s["aw"],away_draws=s["ad"],away_losses=s["al"],
                    clean_sheets=s["cs"],failed_to_score=s["fs"],form="".join(s["r"][-5:])))
            for m in matches:
                db.add(HeadToHead(home_team_id=m.home_team_id,away_team_id=m.away_team_id,
                    match_date=m.kickoff_time,home_score=m.home_score,away_score=m.away_score,
                    sportmonks_fixture_id=m.sportmonks_fixture_id))
            await db.commit()

            # train
            eng = FeatureEngineer(db)
            Xl,yw,yh,yg=[],[],[],[]
            for i,m in enumerate(matches):
                if (i+1)%1000==0: print(f"  特征: {i+1}/{len(matches)}")
                try:
                    f=await eng.extract_features(m.id)
                    if f.empty: continue
                    Xl.append(f)
                    yw.append(0 if m.home_score>m.away_score else 1 if m.home_score==m.away_score else 2)
                    adj=m.home_score+(m.handicap_line or 0)
                    yh.append(0 if adj>m.away_score else 1 if adj==m.away_score else 2)
                    yg.append(m.home_score+m.away_score)
                except: continue
            
            X=pd.concat(Xl).fillna(0.0)
            yw=np.array(yw);yh=np.array(yh);yg=np.array(yg)
            sp=int(len(X)*0.8)
            
            wc=Counter(yw)
            print(f"有效: {len(X)}, 维度: {X.shape[1]}, 主={wc[0]}平={wc[1]}客={wc[2]}基线={wc[0]/len(yw):.3f}")
            
            from sklearn.metrics import accuracy_score
            ma=ModelA();ma.train(X[:sp],yw[:sp],yh[:sp])
            aw=accuracy_score(yw[sp:],ma.model_wl.predict(X[sp:]))
            ah=accuracy_score(yh[sp:],ma.model_hcp.predict(X[sp:]))
            print(f"胜平负: {aw:.4f} 让球: {ah:.4f}")
            
            mk=yg[:sp]>0
            mb=ModelB();mb.train(X[:sp][mk],yg[:sp][mk])
            mae=np.abs(yg[sp:]-mb.model.predict(X[sp:])).mean()
            print(f"进球MAE: {mae:.4f}")
            
            joblib.dump(ma.model_wl,os.path.join(MODEL_DIR,"model_a_wl.pkl"))
            joblib.dump(ma.model_hcp,os.path.join(MODEL_DIR,"model_a_hcp.pkl"))
            joblib.dump(mb.model,os.path.join(MODEL_DIR,"model_b.pkl"))
            
            import shutil
            for f in ["model_a_wl.pkl","model_a_hcp.pkl","model_b.pkl"]:
                try:shutil.copy2(os.path.join(MODEL_DIR,f),os.path.join("..","models",f))
                except:pass
            
            print(f"\n完成! 胜平负={aw:.4f} MAE={mae:.4f}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
