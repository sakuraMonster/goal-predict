"""全联赛训练：拉取全部10个联赛2025-26数据 + 重训练 + 批量预测"""
import asyncio, os, sys, calendar
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import joblib
from datetime import datetime

from app.db.database import async_session
from app.db.models import Match, Team, League, TeamSeasonStats, HeadToHead, Prediction
from app.collector.sportmonks.client import SportMonksClient
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB
from sqlalchemy import select, delete, update

MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)

# 本地联赛名 → SportMonks league_id 映射
LOCAL_TO_SM = {
    1: 8,    # 英超
    2: 564,  # 西甲
    3: 82,   # 德甲
    4: 384,  # 意甲
    5: 301,  # 法甲
    6: 1561, # 韩K
    7: 1558, # 日职联
    9: 598,  # 瑞典超
    10: 592, # 芬超
    11: 597, # 挪超
    12: 262, # 美职联
    13: 605, # 巴西甲
}


async def update_leagues(db):
    """更新所有联赛的 sportmonks_id"""
    for local_id, sm_id in LOCAL_TO_SM.items():
        await db.execute(
            update(League).where(League.id == local_id).values(sportmonks_id=sm_id)
        )
    await db.commit()
    print(f"已更新 {len(LOCAL_TO_SM)} 个联赛的 sportmonks_id")


async def pull_all_fixtures(db, client):
    """拉取2025-2026赛季所有比赛的 fixtures（一次拉覆盖全联赛）"""
    print("拉取比赛数据（2025-08 ~ 2026-05）...")
    
    sm_to_local = {sm: local for local, sm in LOCAL_TO_SM.items()}
    all_fixtures = []
    months = [(2025, m) for m in range(8, 13)] + [(2026, m) for m in range(1, 6)]
    
    for yr, mo in months:
        from_date = f"{yr}-{mo:02d}-01"
        to_date = f"{yr}-{mo:02d}-{calendar.monthrange(yr, mo)[1]}"
        try:
            fixtures = await client.get_fixtures_between(from_date, to_date, "participants;scores;league")
            all_fixtures.extend(fixtures)
            print(f"  {from_date[:7]}: {len(fixtures)} 场")
        except Exception as e:
            print(f"  {from_date[:7]}: 跳过 ({e})")
    
    print(f"  总计: {len(all_fixtures)} 场")
    
    # 获取球队映射
    teams_result = await db.execute(select(Team))
    team_map = {t.sportmonks_id: t for t in teams_result.scalars().all()}
    
    new_matches = 0
    for f in all_fixtures:
        fixture_id = f["id"]
        existing = await db.execute(select(Match).where(Match.sportmonks_fixture_id == fixture_id))
        if existing.scalar_one_or_none():
            continue
        
        # 联赛归属
        sm_league_id = f.get("league_id") or (f.get("league", {}) or {}).get("id")
        local_league = sm_to_local.get(sm_league_id)
        if not local_league:
            continue  # 不在目标联赛列表中
        
        participants = f.get("participants", [])
        home_p = next((p for p in participants if (p.get("meta", {}) or {}).get("location") == "home"), None)
        away_p = next((p for p in participants if (p.get("meta", {}) or {}).get("location") == "away"), None)
        if not home_p or not away_p:
            continue
        
        h_id, a_id = home_p["id"], away_p["id"]
        
        # 球队不存在则自动创建
        for pid, pname in [(h_id, home_p.get("name", "?")), (a_id, away_p.get("name", "?"))]:
            if pid not in team_map:
                t = Team(
                    sportmonks_id=pid, league_id=local_league,
                    name_zh=pname, name_en=pname,
                    short_en=pname[:3],
                )
                db.add(t)
                await db.flush()
                team_map[pid] = t
        
        # 比分
        h_score = a_score = None
        for s in f.get("scores", []):
            if s.get("description") in ("CURRENT", "FT"):
                g = (s.get("score") or {}).get("goals")
                if g is not None:
                    if s.get("participant_id") == h_id: h_score = int(g)
                    else: a_score = int(g)
        
        kickoff = f.get("starting_at")
        if kickoff:
            kickoff = datetime.fromisoformat(kickoff.replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            continue
        
        m = Match(
            sportmonks_fixture_id=fixture_id,
            league_id=local_league,
            home_team_id=team_map[h_id].id, away_team_id=team_map[a_id].id,
            home_team_name=home_p.get("name", "?"), away_team_name=away_p.get("name", "?"),
            kickoff_time=kickoff,
            home_score=h_score, away_score=a_score,
            status="finished" if h_score is not None else "scheduled",
        )
        db.add(m)
        new_matches += 1
        
        if new_matches % 200 == 0:
            await db.commit()
    
    await db.commit()
    print(f"  新增: {new_matches}")
    
    # 修正现有 fixture 的 league_id
    fixtures_with_league = [f for f in all_fixtures if f.get("league_id")]
    for f in fixtures_with_league:
        sm_league = f.get("league_id")
        local = sm_to_local.get(sm_league)
        if local:
            await db.execute(
                update(Match).where(Match.sportmonks_fixture_id == f["id"]).values(league_id=local)
            )
    await db.commit()
    print(f"  联赛归属已修正")


async def compute_stats(db):
    """重算所有 TeamSeasonStats 和 HeadToHead"""
    print("\n重算特征数据...")
    await db.execute(delete(TeamSeasonStats))
    await db.execute(delete(HeadToHead))
    
    result = await db.execute(
        select(Match).where(
            Match.home_score.isnot(None),
            Match.home_team_id.isnot(None),
            Match.away_team_id.isnot(None),
        ).order_by(Match.kickoff_time)
    )
    matches = list(result.scalars().all())
    
    # TeamSeasonStats
    stats = {}
    for m in matches:
        season = str(m.kickoff_time.year) if m.kickoff_time else "2025"
        for tid, ih, gf, ga in [
            (m.home_team_id, True, m.home_score, m.away_score),
            (m.away_team_id, False, m.away_score, m.home_score),
        ]:
            k = (tid, season)
            if k not in stats:
                stats[k] = {"team_id": tid, "season": season, "league_id": m.league_id,
                    "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0,
                    "hw": 0, "hd": 0, "hl": 0, "aw": 0, "ad": 0, "al": 0,
                    "cs": 0, "fs": 0, "r": []}
            s = stats[k]; s["p"] += 1; s["gf"] += gf; s["ga"] += ga
            if gf > ga: s["w"] += 1; s["r"].append("W")
            elif gf == ga: s["d"] += 1; s["r"].append("D")
            else: s["l"] += 1; s["r"].append("L")
            if ih:
                if gf > ga: s["hw"] += 1
                elif gf == ga: s["hd"] += 1
                else: s["hl"] += 1
            else:
                if gf > ga: s["aw"] += 1
                elif gf == ga: s["ad"] += 1
                else: s["al"] += 1
            if ga == 0: s["cs"] += 1
            if gf == 0: s["fs"] += 1
    
    for (tid, season), s in stats.items():
        db.add(TeamSeasonStats(
            team_id=tid, season=season, league_id=s["league_id"],
            played=s["p"], wins=s["w"], draws=s["d"], losses=s["l"],
            goals_for=s["gf"], goals_against=s["ga"],
            home_wins=s["hw"], home_draws=s["hd"], home_losses=s["hl"],
            away_wins=s["aw"], away_draws=s["ad"], away_losses=s["al"],
            clean_sheets=s["cs"], failed_to_score=s["fs"],
            form="".join(s["r"][-5:]),
        ))
    print(f"  TeamSeasonStats: {len(stats)} 条")
    
    for m in matches:
        db.add(HeadToHead(
            home_team_id=m.home_team_id, away_team_id=m.away_team_id,
            match_date=m.kickoff_time, competition="",
            home_score=m.home_score, away_score=m.away_score,
            sportmonks_fixture_id=m.sportmonks_fixture_id,
        ))
    print(f"  HeadToHead: {len(matches)} 条")
    await db.commit()


async def train_and_predict(db):
    """训练 + 批量预测"""
    result = await db.execute(
        select(Match).where(
            Match.home_score.isnot(None),
            Match.home_team_id.isnot(None),
            Match.away_team_id.isnot(None),
        ).order_by(Match.kickoff_time)
    )
    matches = list(result.scalars().all())
    total = len(matches)
    
    # 按联赛统计
    league_counts = {}
    for m in matches:
        league_counts[m.league_id] = league_counts.get(m.league_id, 0) + 1
    print(f"\n训练样本: {total} 场")
    for lid, cnt in sorted(league_counts.items()):
        print(f"  league_id={lid}: {cnt} 场")
    
    engineer = FeatureEngineer(db)
    X_list, y_wl_list, y_hcp_list, y_goals_list = [], [], [], []
    
    for i, m in enumerate(matches):
        if (i + 1) % 500 == 0:
            print(f"  提取特征: {i+1}/{total}")
        try:
            feats = await engineer.extract_features(m.id)
            if feats.empty: continue
            X_list.append(feats)
            y_wl_list.append(0 if m.home_score > m.away_score else 1 if m.home_score == m.away_score else 2)
            adj = m.home_score + (m.handicap_line or 0)
            y_hcp_list.append(0 if adj > m.away_score else 1 if adj == m.away_score else 2)
            y_goals_list.append(m.home_score + m.away_score)
        except: continue
    
    X = pd.concat(X_list, ignore_index=True).fillna(0.0)
    y_wl = np.array(y_wl_list); y_hcp = np.array(y_hcp_list); y_goals = np.array(y_goals_list)
    print(f"  有效样本: {len(X)} 场, 维度: {X.shape[1]}")
    
    from collections import Counter
    wl_dist = Counter(y_wl)
    print(f"  主胜={wl_dist[0]} 平局={wl_dist[1]} 客胜={wl_dist[2]}, 基线={wl_dist[0]/len(y_wl):.3f}")
    
    split = int(len(X) * 0.8)
    Xt, Xe = X.iloc[:split], X.iloc[split:]
    
    # Model A
    print("\n训练 Model A...")
    ma = ModelA()
    ma.train(Xt, y_wl[:split], y_hcp[:split])
    from sklearn.metrics import accuracy_score
    a_wl = accuracy_score(y_wl[split:], ma.model_wl.predict(Xe))
    a_hcp = accuracy_score(y_hcp[split:], ma.model_hcp.predict(Xe))
    print(f"  胜平负: {a_wl:.4f}  让球: {a_hcp:.4f}")
    
    # Model B
    print("训练 Model B...")
    msk = y_goals[:split] > 0
    mb = ModelB()
    mb.train(Xt[msk], y_goals[:split][msk])
    mae = np.abs(y_goals[split:] - mb.model.predict(Xe)).mean()
    print(f"  进球MAE: {mae:.4f}")
    
    # 保存
    joblib.dump(ma.model_wl, os.path.join(MODEL_DIR, "model_a_wl.pkl"))
    joblib.dump(ma.model_hcp, os.path.join(MODEL_DIR, "model_a_hcp.pkl"))
    joblib.dump(mb.model, os.path.join(MODEL_DIR, "model_b.pkl"))
    print(f"\n模型已保存")
    
    # 批量预测
    print("批量预测...")
    all_m = await db.execute(select(Match).where(Match.home_team_id.isnot(None), Match.away_team_id.isnot(None)))
    all_matches = all_m.scalars().all()
    version = datetime.now().strftime("%Y%m%d-%H%M")
    
    # Recreate pipeline with new models
    from app.predictor.pipeline import PredictionPipeline
    pipeline = PredictionPipeline(db)
    
    new_p, upd_p = 0, 0
    for i, m in enumerate(all_matches):
        if (i + 1) % 500 == 0: print(f"  预测: {i+1}/{len(all_matches)}")
        try:
            r = await pipeline.predict(m.id)
        except: continue
        ex = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
        p = ex.scalar_one_or_none()
        if p:
            for k in ["home_prob","draw_prob","away_prob","handicap_home_prob","handicap_draw_prob","handicap_away_prob",
                       "expected_goals","over_2_5_prob","goal_distribution","score_top5_json",
                       "confidence_level","is_cold_match","summary_text","key_factors"]:
                setattr(p, k, r[k])
            p.model_version = version
            upd_p += 1
        else:
            db.add(Prediction(match_id=m.id, model_version=version, **r))
            new_p += 1
        if (i + 1) % 200 == 0: await db.commit()
    await db.commit()
    print(f"  预测: 新增 {new_p}, 更新 {upd_p}")
    
    return {"samples": len(X), "acc_wl": a_wl, "acc_hcp": a_hcp, "mae": mae, "version": version}


async def main():
    client = SportMonksClient()
    try:
        async with async_session() as db:
            print("=" * 50)
            print("Step 1: 更新联赛 SportMonks ID")
            await update_leagues(db)
            
            print("\nStep 2: 拉取全联赛比赛")
            await pull_all_fixtures(db, client)
            
            print("\nStep 3: 重算特征")
            await compute_stats(db)
            
            print("\nStep 4: 训练+预测")
            result = await train_and_predict(db)
            
            print("\n" + "=" * 50)
            print(f"完成! 版本: {result['version']}")
            print(f"样本: {result['samples']} 场")
            print(f"胜平负: {result['acc_wl']:.4f}  让球: {result['acc_hcp']:.4f}  进球MAE: {result['mae']:.4f}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
