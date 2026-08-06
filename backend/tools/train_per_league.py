"""分联赛独立拉取 + 全量重训练"""
import asyncio, os, sys, calendar
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import joblib
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

# 联赛配置：本地ID → (SportMonks league_id, 名称, season_id)
LEAGUES = {
    1:  (8,    "英超",    25583),
    2:  (564,  "西甲",    None),  # season_id 需查询
    3:  (82,   "德甲",    25646),
    4:  (384,  "意甲",    None),
    5:  (301,  "法甲",    None),
    6:  (1561, "韩K",     None),
    7:  (1558, "日职联",  None),
    9:  (598,  "瑞典超",  None),
    10: (592,  "芬超",    None),
    11: (597,  "挪超",    None),
    12: (262,  "美职联",  None),
    13: (605,  "巴西甲",  None),
}


async def get_season_ids(client):
    """查询各联赛的 season_id"""
    for local_id, (sm_id, name, _) in LEAGUES.items():
        if LEAGUES[local_id][2] is not None:
            continue  # 已知
        try:
            data = await client._get(f"/leagues/{sm_id}", params={"include": "seasons"})
            seasons = data.get("data", {}).get("seasons", [])
            for s in seasons:
                if s["name"] == "2025/2026":
                    LEAGUES[local_id] = (sm_id, name, s["id"])
                    print(f"  {name}: season_id={s['id']}")
                    break
        except Exception as e:
            print(f"  {name}: 查询失败 ({e})")


async def pull_league_fixtures(db, client, local_id, sm_id, season_id, name):
    """逐个联赛拉取 fixtures（按周分批，避开 500 限制）"""
    print(f"  [{name}] 拉取中...")
    
    # 先同步球队
    try:
        teams = await client.get_teams_by_season(season_id)
    except:
        teams = []
    
    team_map = {}
    for p in teams:
        sm_tid = p["id"]
        ex = await db.execute(select(Team).where(Team.sportmonks_id == sm_tid))
        t = ex.scalar_one_or_none()
        if not t:
            t = Team(sportmonks_id=sm_tid, league_id=local_id,
                     name_zh=p.get("name",""), name_en=p.get("name",""),
                     short_en=p.get("short_code", p.get("name","")[:3]),
                     logo_url=p.get("image_path",""))
            db.add(t); await db.flush()
        team_map[sm_tid] = t
    
    # 按周拉取 fixtures
    start = datetime(2025, 8, 1)
    end = datetime(2026, 6, 1)
    current = start
    new_count = 0
    
    while current < end:
        week_end = current + timedelta(days=7)
        from_d = current.strftime("%Y-%m-%d")
        to_d = min(week_end, end).strftime("%Y-%m-%d")
        
        try:
            fixtures = await client.get_fixtures_between(from_d, to_d, "participants;scores")
        except:
            current = week_end
            continue
        
        for f in fixtures:
            fid = f["id"]
            ex = await db.execute(select(Match).where(Match.sportmonks_fixture_id == fid))
            if ex.scalar_one_or_none():
                continue
            
            participants = f.get("participants", [])
            hp = next((p for p in participants if (p.get("meta",{}) or {}).get("location")=="home"), None)
            ap = next((p for p in participants if (p.get("meta",{}) or {}).get("location")=="away"), None)
            if not hp or not ap: continue
            
            hid, aid = hp["id"], ap["id"]
            
            # 球队不在映射中则跳过（非本联赛球队）
            if hid not in team_map or aid not in team_map:
                continue
            
            hs = as_ = None
            for s in f.get("scores", []):
                if s.get("description") in ("CURRENT","FT"):
                    g = (s.get("score") or {}).get("goals")
                    if g is not None:
                        if s.get("participant_id") == hid: hs = int(g)
                        else: as_ = int(g)
            
            ko = f.get("starting_at")
            if ko:
                ko = datetime.fromisoformat(ko.replace("Z","+00:00")).replace(tzinfo=None)
            else: continue
            
            db.add(Match(
                sportmonks_fixture_id=fid, league_id=local_id,
                home_team_id=team_map[hid].id, away_team_id=team_map[aid].id,
                home_team_name=hp.get("name","?"), away_team_name=ap.get("name","?"),
                kickoff_time=ko, home_score=hs, away_score=as_,
                status="finished" if hs is not None else "scheduled",
            ))
            new_count += 1
        
        if new_count > 0:
            await db.commit()
        current = week_end
    
    print(f"    {name}: +{new_count} 场")


async def compute_stats(db):
    """重算特征"""
    print("\n重算 TeamSeasonStats + HeadToHead...")
    await db.execute(delete(TeamSeasonStats))
    await db.execute(delete(HeadToHead))
    
    result = await db.execute(
        select(Match).where(Match.home_score.isnot(None),
                            Match.home_team_id.isnot(None),
                            Match.away_team_id.isnot(None))
        .order_by(Match.kickoff_time))
    matches = list(result.scalars().all())
    
    stats = {}
    for m in matches:
        season = str(m.kickoff_time.year) if m.kickoff_time else "2025"
        for tid, ih, gf, ga in [(m.home_team_id, True, m.home_score, m.away_score),
                                  (m.away_team_id, False, m.away_score, m.home_score)]:
            k = (tid, season)
            if k not in stats:
                stats[k] = {"team_id": tid, "season": season, "league_id": m.league_id,
                    "p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,
                    "hw":0,"hd":0,"hl":0,"aw":0,"ad":0,"al":0,"cs":0,"fs":0,"r":[]}
            s = stats[k]; s["p"]+=1; s["gf"]+=gf; s["ga"]+=ga
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
    
    for (tid, sea), s in stats.items():
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
    print(f"  TeamSeasonStats: {len(stats)}, HeadToHead: {len(matches)}")


async def train_and_predict(db):
    """训练 + 预测"""
    result = await db.execute(
        select(Match).where(Match.home_score.isnot(None),
                            Match.home_team_id.isnot(None),
                            Match.away_team_id.isnot(None))
        .order_by(Match.kickoff_time))
    matches = list(result.scalars().all())
    
    # 统计
    from collections import Counter
    league_cnt = Counter(m.league_id for m in matches)
    print(f"\n训练样本: {len(matches)} 场")
    for lid in sorted(league_cnt):
        print(f"  league_id={lid}: {league_cnt[lid]} 场")
    
    engineer = FeatureEngineer(db)
    Xl, ywl, yhc, yg = [], [], [], []
    for i, m in enumerate(matches):
        if (i+1)%500==0: print(f"  特征: {i+1}/{len(matches)}")
        try:
            f = await engineer.extract_features(m.id)
            if f.empty: continue
            Xl.append(f)
            ywl.append(0 if m.home_score>m.away_score else 1 if m.home_score==m.away_score else 2)
            adj = m.home_score+(m.handicap_line or 0)
            yhc.append(0 if adj>m.away_score else 1 if adj==m.away_score else 2)
            yg.append(m.home_score+m.away_score)
        except: continue
    
    X = pd.concat(Xl, ignore_index=True).fillna(0.0)
    ywl=np.array(ywl); yhc=np.array(yhc); yg=np.array(yg)
    print(f"  有效: {len(X)} 场, 维度: {X.shape[1]}")
    
    wc = Counter(ywl)
    print(f"  分布: 主胜={wc[0]} 平={wc[1]} 客={wc[2]}, 基线={wc[0]/len(ywl):.3f}")
    
    sp = int(len(X)*0.8)
    
    print("\n训练 Model A...")
    ma = ModelA(); ma.train(X[:sp], ywl[:sp], yhc[:sp])
    from sklearn.metrics import accuracy_score
    aw = accuracy_score(ywl[sp:], ma.model_wl.predict(X[sp:]))
    ah = accuracy_score(yhc[sp:], ma.model_hcp.predict(X[sp:]))
    print(f"  胜平负: {aw:.4f}  让球: {ah:.4f}")
    
    print("训练 Model B...")
    msk = yg[:sp] > 0
    mb = ModelB(); mb.train(X[:sp][msk], yg[:sp][msk])
    mae = np.abs(yg[sp:] - mb.model.predict(X[sp:])).mean()
    print(f"  进球MAE: {mae:.4f}")
    
    joblib.dump(ma.model_wl, os.path.join(MODEL_DIR, "model_a_wl.pkl"))
    joblib.dump(ma.model_hcp, os.path.join(MODEL_DIR, "model_a_hcp.pkl"))
    joblib.dump(mb.model, os.path.join(MODEL_DIR, "model_b.pkl"))
    
    # 批量预测
    print("\n批量预测...")
    from app.predictor.pipeline import PredictionPipeline
    pl = PredictionPipeline(db)
    ver = datetime.now().strftime("%Y%m%d-%H%M")
    all_m = await db.execute(select(Match).where(Match.home_team_id.isnot(None), Match.away_team_id.isnot(None)))
    
    nw, up = 0, 0
    for i, m in enumerate(all_m.scalars().all()):
        if (i+1)%500==0: print(f"  预测: {i+1}")
        try: r = await pl.predict(m.id)
        except: continue
        ex = await db.execute(select(Prediction).where(Prediction.match_id==m.id))
        p = ex.scalar_one_or_none()
        keys = ["home_prob","draw_prob","away_prob","handicap_home_prob","handicap_draw_prob","handicap_away_prob",
                "expected_goals","over_2_5_prob","goal_distribution","score_top5_json",
                "confidence_level","is_cold_match","summary_text","key_factors"]
        if p:
            for k in keys: setattr(p, k, r.get(k))
            p.model_version = ver; up += 1
        else:
            db.add(Prediction(match_id=m.id, model_version=ver, **{k: r.get(k) for k in keys}))
            nw += 1
        if (i+1)%200==0: await db.commit()
    await db.commit()
    print(f"  新增 {nw}, 更新 {up}")
    
    # 复制模型到正确目录
    import shutil
    for f in ["model_a_wl.pkl","model_a_hcp.pkl","model_b.pkl"]:
        src = os.path.join(MODEL_DIR, f)
        if os.path.exists(src) and MODEL_DIR != "backend/models":
            dst = os.path.join("backend/models", f) if os.path.exists("backend") else src
            try: shutil.copy2(src, os.path.join("..", "models", f))
            except: pass
    
    return {"samples": len(X), "aw": aw, "ah": ah, "mae": mae}


async def main():
    client = SportMonksClient()
    try:
        async with async_session() as db:
            print("="*50)
            print("Step 1: 更新联赛 + 查询 season_id")
            for lid, (sm_id, name, _) in LEAGUES.items():
                await db.execute(update(League).where(League.id==lid).values(sportmonks_id=sm_id))
            await db.commit()
            await get_season_ids(client)
            
            print("\nStep 2: 分联赛拉取 (按周)")
            for local_id, (sm_id, name, season_id) in LEAGUES.items():
                if season_id is None:
                    print(f"  [{name}] season_id 缺失，跳过")
                    continue
                await pull_league_fixtures(db, client, local_id, sm_id, season_id, name)
            
            print("\nStep 3: 重算特征")
            await compute_stats(db)
            
            print("\nStep 4: 训练 + 预测")
            r = await train_and_predict(db)
            
            print("\n" + "="*50)
            print(f"完成! 样本: {r['samples']} 场")
            print(f"胜平负: {r['aw']:.4f}  让球: {r['ah']:.4f}  进球MAE: {r['mae']:.4f}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
