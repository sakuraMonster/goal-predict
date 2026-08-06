"""拉取英超2025/26赛季数据并训练模型A+B"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import joblib
from datetime import datetime

from app.db.database import async_session, engine
from app.db.models import Match, Team, League, TeamSeasonStats, Prediction
from app.collector.sportmonks.client import SportMonksClient
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB
from sqlalchemy import select, update

SM_LEAGUE_ID = 8  # SportMonks Premier League ID
SEASON_ID = 25583  # 2025/2026
LOCAL_LEAGUE_ID = 1  # 本地联赛ID


async def update_league(db):
    """更新英超的 SportMonks ID"""
    await db.execute(
        update(League).where(League.id == LOCAL_LEAGUE_ID).values(sportmonks_id=SM_LEAGUE_ID)
    )
    await db.commit()


async def sync_teams(db, client):
    """同步英超球队"""
    print("拉取球队列表...")
    teams_data = await client.get_teams_by_season(SEASON_ID)
    participants = teams_data  # get_teams_by_season 已返回 list of dicts
    
    teams_created = 0
    for p in participants:
        sm_id = p["id"]
        existing = await db.execute(select(Team).where(Team.sportmonks_id == sm_id))
        if existing.scalar_one_or_none():
            continue
        
        name = p.get("name", "")
        short = p.get("short_code", name[:3])
        logo = p.get("image_path", "")
        
        team = Team(
            sportmonks_id=sm_id,
            league_id=LOCAL_LEAGUE_ID,
            name_zh=name,  # 先用英文名，后续通过映射更新中文
            name_en=name,
            short_en=short,
            logo_url=logo,
        )
        db.add(team)
        teams_created += 1
    
    await db.commit()
    print(f"  新增球队: {teams_created}, 总计: {len(participants)}")


async def sync_fixtures(db, client):
    """拉取英超2025/26赛季所有 fixtures（含比分）"""
    print("拉取比赛数据...")
    
    # 分批拉取：按月份遍历2025-08到2026-05
    all_fixtures = []
    months = [(2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
              (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5)]
    
    for yr, mo in months:
        from_date = f"{yr}-{mo:02d}-01"
        # 月末
        import calendar
        last_day = calendar.monthrange(yr, mo)[1]
        to_date = f"{yr}-{mo:02d}-{last_day}"
        
        try:
            fixtures = await client.get_fixtures_between(from_date, to_date, "participants;scores")
            all_fixtures.extend(fixtures)
            print(f"  {from_date[:7]}: {len(fixtures)} 场")
        except Exception as e:
            print(f"  {from_date[:7]}: 跳过 ({e})")
            continue
    
    print(f"  共 {len(all_fixtures)} 场比赛")
    
    # 获取球队映射
    teams_result = await db.execute(select(Team))
    team_map = {t.sportmonks_id: t for t in teams_result.scalars().all()}
    
    matches_created = 0
    for f in all_fixtures:
        fixture_id = f["id"]
        
        # 检查是否已存在
        existing = await db.execute(
            select(Match).where(Match.sportmonks_fixture_id == fixture_id)
        )
        if existing.scalar_one_or_none():
            continue
        
        participants = f.get("participants", [])
        home_p, away_p = None, None
        for p in participants:
            meta = p.get("meta", {})
            if meta.get("location") == "home":
                home_p = p
            else:
                away_p = p
        
        if not home_p or not away_p:
            continue
        
        home_sm_id = home_p["id"]
        away_sm_id = away_p["id"]
        home_name = home_p.get("name", "Unknown")
        away_name = away_p.get("name", "Unknown")
        
        # 比分
        home_score = None
        away_score = None
        for s in f.get("scores", []):
            desc = s.get("description", "")
            if desc in ("CURRENT", "FT"):
                goals = (s.get("score") or {}).get("goals")
                if goals is not None:
                    if s.get("participant_id") == home_sm_id:
                        home_score = int(goals)
                    else:
                        away_score = int(goals)
        
        # 开球时间
        kickoff = f.get("starting_at")
        if kickoff:
            kickoff = datetime.fromisoformat(kickoff.replace("Z", "+00:00")).replace(tzinfo=None)
        else:
            continue
        
        m = Match(
            sportmonks_fixture_id=fixture_id,
            league_id=LOCAL_LEAGUE_ID,
            home_team_id=team_map.get(home_sm_id, None).id if home_sm_id in team_map else None,
            away_team_id=team_map.get(away_sm_id, None).id if away_sm_id in team_map else None,
            home_team_name=home_name,
            away_team_name=away_name,
            kickoff_time=kickoff,
            home_score=home_score,
            away_score=away_score,
            status="finished" if home_score is not None else "scheduled",
        )
        db.add(m)
        matches_created += 1
    
    await db.commit()
    
    # 统计
    total = await db.execute(select(Match).where(Match.league_id == LOCAL_LEAGUE_ID))
    scored = await db.execute(
        select(Match).where(Match.league_id == LOCAL_LEAGUE_ID, Match.home_score.isnot(None))
    )
    print(f"  新增: {matches_created}, 总计: {total.scalars().all()}, 有比分: {scored.scalars().all()}")


async def train_models(db):
    """使用已入库数据训练 Model A + B"""
    result = await db.execute(
        select(Match).where(
            Match.home_score.isnot(None),
            Match.home_team_id.isnot(None),
            Match.away_team_id.isnot(None),
        ).order_by(Match.kickoff_time)
    )
    matches = list(result.scalars().all())
    print(f"\n训练样本: {len(matches)} 场")
    
    if len(matches) < 10:
        print("数据不足 (需 ≥10 场)，跳过训练")
        return
    
    # 提取特征和标签
    engineer = FeatureEngineer(db)
    X_list, y_wl_list, y_hcp_list, y_goals_list = [], [], [], []
    
    for i, m in enumerate(matches):
        if (i + 1) % 50 == 0:
            print(f"  提取特征: {i+1}/{len(matches)}")
        try:
            feats = await engineer.extract_features(m.id)
            if feats.empty:
                continue
            X_list.append(feats)
            
            # Model A 标签
            y_wl_list.append(0 if m.home_score > m.away_score else 1 if m.home_score == m.away_score else 2)
            adjusted = m.home_score + (m.handicap_line or 0)
            y_hcp_list.append(0 if adjusted > m.away_score else 1 if adjusted == m.away_score else 2)
            
            # Model B 标签
            y_goals_list.append(m.home_score + m.away_score)
        except Exception as e:
            if i < 5:
                print(f"  警告: match_id={m.id} 特征提取失败: {e}")
            continue
    
    if len(X_list) < 10:
        print(f"有效特征样本: {len(X_list)}，不足训练")
        return
    
    X = pd.concat(X_list, ignore_index=True)
    valid = ~X.isna().any(axis=1)
    X = X[valid]
    
    y_wl = np.array(y_wl_list)[valid.values]
    y_hcp = np.array(y_hcp_list)[valid.values]
    y_goals = np.array(y_goals_list)[valid.values]
    
    print(f"有效样本: {len(X)} 场")
    
    # 训练
    print("\n训练 Model A (LightGBM)...")
    model_a = ModelA()
    model_a.train(X, y_wl, y_hcp)
    
    # 评估
    from sklearn.metrics import accuracy_score
    y_pred_wl = model_a.model_wl.predict(X)
    acc_wl = accuracy_score(y_wl, y_pred_wl)
    y_pred_hcp = model_a.model_hcp.predict(X)
    acc_hcp = accuracy_score(y_hcp, y_pred_hcp)
    print(f"  胜平负准确率: {acc_wl:.3f}")
    print(f"  让球准确率: {acc_hcp:.3f}")
    
    print("\n训练 Model B (Poisson)...")
    y_b_valid = y_goals > 0
    model_b = ModelB()
    model_b.train(X[y_b_valid], y_goals[y_b_valid])
    
    y_pred_goals = model_b.model.predict(X)
    mae = np.abs(y_goals - y_pred_goals).mean()
    print(f"  进球预测 MAE: {mae:.3f}")
    
    # 持久化
    model_dir = "backend/models"
    os.makedirs(model_dir, exist_ok=True)
    
    joblib.dump(model_a.model_wl, os.path.join(model_dir, "model_a_wl.pkl"))
    joblib.dump(model_a.model_hcp, os.path.join(model_dir, "model_a_hcp.pkl"))
    joblib.dump(model_b.model, os.path.join(model_dir, "model_b.pkl"))
    
    # 保存模型版本元信息
    version = datetime.now().strftime("%Y%m%d-%H%M")
    meta = {
        "version": version,
        "samples": len(X),
        "features": list(X.columns),
        "accuracy_wl": round(float(acc_wl), 4),
        "accuracy_hcp": round(float(acc_hcp), 4),
        "mae_goals": round(float(mae), 4),
        "trained_at": datetime.now().isoformat(),
    }
    joblib.dump(meta, os.path.join(model_dir, f"model_meta_{version}.pkl"))
    
    print(f"\n模型已保存: backend/models/")
    print(f"  版本: {version}")
    print(f"  胜平负: {acc_wl:.1%}  让球: {acc_hcp:.1%}  进球MAE: {mae:.2f}")


async def main():
    client = SportMonksClient()
    try:
        async with async_session() as db:
            print("=" * 50)
            print("Step 1: 更新联赛 SportMonks ID")
            await update_league(db)
            
            print("\nStep 2: 同步球队")
            await sync_teams(db, client)
            
            print("\nStep 3: 拉取比赛数据")
            await sync_fixtures(db, client)
            
            print("\nStep 4: 训练模型")
            await train_models(db)
            
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
