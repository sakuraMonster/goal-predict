"""完整重训练：用正确ID拉取缺失7联赛 + 全量训练"""
import asyncio, os, sys, calendar
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

import numpy as np, pandas as pd, joblib
from datetime import datetime, timedelta
from app.db.database import async_session
from app.db.models import Match, Team, League, TeamSeasonStats, HeadToHead, Prediction
from app.collector.sportmonks.client import SportMonksClient
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB
from sqlalchemy import select, delete, update

MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)

# 正确 ID
LEAGUES = {
    6:  (1034, "韩K"),
    7:  (968,  "日职联"),
    9:  (573,  "瑞典超"),
    10: (292,  "芬超"),
    11: (444,  "挪超"),
    12: (779,  "美职联"),
    13: (648,  "巴西甲"),
}


async def get_season(client, sm_id, name):
    try:
        d = await client._get(f'/leagues/{sm_id}', params={'include':'seasons'})
        for s in d.get('data',{}).get('seasons',[]):
            print(f'  {name}: id={s["id"]} name={s["name"]}')
            # 取最近的非 future 赛季
            if not s.get("is_current") and "2025" in s.get("name",""):
                return s["id"]
            if s.get("is_current"):
                return s["id"]  # fallback
    except Exception as e:
        print(f'  {name}: {e}')
    return None


async def pull_league(db, client, local_id, sm_id, name, season_id):
    print(f'  [{name}] season_id={season_id}')
    await db.execute(update(League).where(League.id==local_id).values(sportmonks_id=sm_id))
    await db.commit()
    
    # 加载已有球队映射
    existing = await db.execute(select(Team))
    tmap = {t.sportmonks_id: t for t in existing.scalars().all() if t.sportmonks_id}
    
    # fixtures per 1 day (避免 pagination 慢)
    start = datetime(2025, 1, 1)
    end = datetime(2026, 6, 1)
    cur, added = start, 0
    total_days = (end - start).days
    
    while cur < end:
        nxt = cur + timedelta(days=1)
        day_str = cur.strftime("%Y-%m-%d")
        try:
            fixtures = await client.get_fixtures_by_date(day_str, "participants;scores")
        except Exception as e:
            cur = nxt; continue
        
        batch = 0
        for f in fixtures:
            fid = f["id"]
            ex = await db.execute(select(Match).where(Match.sportmonks_fixture_id==fid))
            existing_match = ex.scalar_one_or_none()
            if existing_match:
                # 修复错误联赛归属
                if existing_match.league_id != local_id:
                    existing_match.league_id = local_id
                    batch += 1
                continue
            
            pp = f.get("participants", [])
            hp = next((p for p in pp if (p.get("meta",{})or{}).get("location")=="home"), None)
            ap = next((p for p in pp if (p.get("meta",{})or{}).get("location")=="away"), None)
            if not hp or not ap: continue
            hid, aid = hp["id"], ap["id"]
            
            for pid, pname in [(hid, hp.get("name","?")), (aid, ap.get("name","?"))]:
                if pid not in tmap:
                    t = Team(sportmonks_id=pid, league_id=local_id,
                             name_zh=pname, name_en=pname,
                             short_en=(pname or "?")[:3])
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
            
            db.add(Match(sportmonks_fixture_id=fid, league_id=local_id,
                home_team_id=tmap[hid].id, away_team_id=tmap[aid].id,
                home_team_name=hp.get("name","?"), away_team_name=ap.get("name","?"),
                kickoff_time=ko, home_score=hs, away_score=asc,
                status="finished" if hs is not None else "scheduled"))
            batch += 1; added += 1
        
        if batch > 0: await db.commit()
        cur = nxt
        if cur.day == 1 and cur.month % 2 == 0:
            print(f'    {name}: {cur.strftime("%Y-%m")} +{added} 场')
    
    print(f'    +{added} 场')
    return added


async def compute_and_train(db):
    await db.execute(delete(TeamSeasonStats))
    await db.execute(delete(HeadToHead))
    
    result = await db.execute(
        select(Match).where(Match.home_score.isnot(None),
                            Match.home_team_id.isnot(None),
                            Match.away_team_id.isnot(None))
        .order_by(Match.kickoff_time))
    matches = list(result.scalars().all())
    
    sdict = {}
    for m in matches:
        sea = str(m.kickoff_time.year) if m.kickoff_time else "2025"
        for tid, ih, gf, ga in [(m.home_team_id,True,m.home_score,m.away_score),
                                  (m.away_team_id,False,m.away_score,m.home_score)]:
            k = (tid, sea)
            if k not in sdict:
                sdict[k] = {"team_id":tid,"season":sea,"league_id":m.league_id,
                    "p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,
                    "hw":0,"hd":0,"hl":0,"aw":0,"ad":0,"al":0,"cs":0,"fs":0,"r":[]}
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
    
    for (tid, sea), s in sdict.items():
        db.add(TeamSeasonStats(team_id=tid, season=sea, league_id=s["league_id"],
            played=s["p"], wins=s["w"], draws=s["d"], losses=s["l"],
            goals_for=s["gf"], goals_against=s["ga"],
            home_wins=s["hw"], home_draws=s["hd"], home_losses=s["hl"],
            away_wins=s["aw"], away_draws=s["ad"], away_losses=s["al"],
            clean_sheets=s["cs"], failed_to_score=s["fs"], form="".join(s["r"][-5:])))
    
    for m in matches:
        db.add(HeadToHead(home_team_id=m.home_team_id, away_team_id=m.away_team_id,
            match_date=m.kickoff_time, home_score=m.home_score, away_score=m.away_score,
            sportmonks_fixture_id=m.sportmonks_fixture_id))
    await db.commit()
    
    from collections import Counter
    lc = Counter(m.league_id for m in matches)
    print(f'\n训练样本: {len(matches)} 场')
    for lid in sorted(lc): print(f'  league_id={lid}: {lc[lid]} 场')
    
    eng = FeatureEngineer(db)
    Xl, yw, yh, yg = [], [], [], []
    for i, m in enumerate(matches):
        if (i+1)%800==0: print(f'  特征: {i+1}/{len(matches)}')
        try:
            f = await eng.extract_features(m.id)
            if f.empty: continue
            Xl.append(f)
            yw.append(0 if m.home_score>m.away_score else 1 if m.home_score==m.away_score else 2)
            adj = m.home_score+(m.handicap_line or 0)
            yh.append(0 if adj>m.away_score else 1 if adj==m.away_score else 2)
            yg.append(m.home_score+m.away_score)
        except: continue
    
    X = pd.concat(Xl).fillna(0.0)
    yw=np.array(yw); yh=np.array(yh); yg=np.array(yg)
    
    wc = Counter(yw)
    sp = int(len(X)*0.8)
    
    print(f'\n有效: {len(X)}, 维度: {X.shape[1]}, 主胜={wc[0]} 平={wc[1]} 客={wc[2]}')
    
    ma = ModelA(); ma.train(X[:sp], yw[:sp], yh[:sp])
    from sklearn.metrics import accuracy_score
    aw = accuracy_score(yw[sp:], ma.model_wl.predict(X[sp:]))
    ah = accuracy_score(yh[sp:], ma.model_hcp.predict(X[sp:]))
    print(f'胜平负: {aw:.4f}  让球: {ah:.4f}  基线: {wc[0]/len(yw):.3f}')
    
    mk = yg[:sp] > 0
    mb = ModelB(); mb.train(X[:sp][mk], yg[:sp][mk])
    mae = np.abs(yg[sp:] - mb.model.predict(X[sp:])).mean()
    print(f'进球MAE: {mae:.4f}')
    
    joblib.dump(ma.model_wl, os.path.join(MODEL_DIR, "model_a_wl.pkl"))
    joblib.dump(ma.model_hcp, os.path.join(MODEL_DIR, "model_a_hcp.pkl"))
    joblib.dump(mb.model, os.path.join(MODEL_DIR, "model_b.pkl"))
    
    # 预测
    from app.predictor.pipeline import PredictionPipeline
    pl = PredictionPipeline(db)
    ver = datetime.now().strftime("%Y%m%d-%H%M")
    all_m = await db.execute(select(Match).where(Match.home_team_id.isnot(None), Match.away_team_id.isnot(None)))
    nw, up = 0, 0
    for i, m in enumerate(all_m.scalars().all()):
        if (i+1)%600==0: print(f'预测: {i+1}')
        try: r = await pl.predict(m.id)
        except: continue
        ex = await db.execute(select(Prediction).where(Prediction.match_id==m.id))
        p = ex.scalar_one_or_none()
        ks = ["home_prob","draw_prob","away_prob","handicap_home_prob","handicap_draw_prob","handicap_away_prob",
              "expected_goals","over_2_5_prob","goal_distribution","score_top5_json","confidence_level","is_cold_match","summary_text","key_factors"]
        if p:
            for k in ks: setattr(p, k, r.get(k))
            p.model_version = ver; up += 1
        else:
            db.add(Prediction(match_id=m.id, model_version=ver, **{k: r.get(k) for k in ks}))
            nw += 1
        if (i+1)%200==0: await db.commit()
    await db.commit()
    
    import shutil
    for f in ["model_a_wl.pkl","model_a_hcp.pkl","model_b.pkl"]:
        try: shutil.copy2(os.path.join(MODEL_DIR,f), os.path.join("..","models",f))
        except: pass
    
    return {"samples": len(X), "aw": aw, "ah": ah, "mae": mae}


async def main():
    client = SportMonksClient()
    try:
        async with async_session() as db:
            print("查询正确 season_id...")
            seasons = {}
            for lid, (sm_id, name) in LEAGUES.items():
                sid = await get_season(client, sm_id, name)
                if sid: seasons[lid] = (sm_id, name, sid)
            
            print(f'\n拉取 {len(seasons)} 个联赛...')
            for lid, (sm_id, name, sid) in seasons.items():
                await pull_league(db, client, lid, sm_id, name, sid)
            
            print('\n训练 + 预测')
            r = await compute_and_train(db)
            print(f'\n完成! 样本={r["samples"]}, 胜平负={r["aw"]:.4f}, MAE={r["mae"]:.4f}')
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
