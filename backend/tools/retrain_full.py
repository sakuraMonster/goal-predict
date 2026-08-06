"""完整重新训练：从已有比赛数据计算所有特征，训练模型A+B"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import joblib
from datetime import datetime
from collections import defaultdict

from app.db.database import async_session
from app.db.models import Match, Team, TeamSeasonStats, HeadToHead
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB
from sqlalchemy import select, delete


async def compute_team_stats(db):
    """从已有比赛数据计算 TeamSeasonStats"""
    print("\n=== Step 1: 计算球队赛季统计 ===")
    
    # 清空旧数据
    await db.execute(delete(TeamSeasonStats))
    
    result = await db.execute(
        select(Match).where(
            Match.home_score.isnot(None),
            Match.home_team_id.isnot(None),
            Match.away_team_id.isnot(None),
        ).order_by(Match.kickoff_time)
    )
    matches = list(result.scalars().all())
    
    # 按 team_id + season 聚合
    stats_map = {}  # (team_id, season) -> dict
    
    for m in matches:
        season = str(m.kickoff_time.year) if m.kickoff_time else "2025"
        
        for team_id, is_home, gf, ga, opp_id in [
            (m.home_team_id, True, m.home_score, m.away_score, m.away_team_id),
            (m.away_team_id, False, m.away_score, m.home_score, m.home_team_id),
        ]:
            key = (team_id, season)
            if key not in stats_map:
                stats_map[key] = {
                    "team_id": team_id, "season": season, "league_id": m.league_id,
                    "played": 0, "wins": 0, "draws": 0, "losses": 0,
                    "goals_for": 0, "goals_against": 0,
                    "home_wins": 0, "home_draws": 0, "home_losses": 0,
                    "away_wins": 0, "away_draws": 0, "away_losses": 0,
                    "clean_sheets": 0, "failed_to_score": 0,
                    "results": [],  # for form calculation
                }
            s = stats_map[key]
            s["played"] += 1
            s["goals_for"] += gf
            s["goals_against"] += ga
            
            if gf > ga:
                s["wins"] += 1
                s["results"].append("W")
                if is_home: s["home_wins"] += 1
                else: s["away_wins"] += 1
            elif gf == ga:
                s["draws"] += 1
                s["results"].append("D")
                if is_home: s["home_draws"] += 1
                else: s["away_draws"] += 1
            else:
                s["losses"] += 1
                s["results"].append("L")
                if is_home: s["home_losses"] += 1
                else: s["away_losses"] += 1
            
            if ga == 0: s["clean_sheets"] += 1
            if gf == 0: s["failed_to_score"] += 1
    
    created = 0
    for (team_id, season), s in stats_map.items():
        # form = last 5 results
        form = "".join(s["results"][-5:]) if s["results"] else ""
        
        stat = TeamSeasonStats(
            team_id=team_id, season=season, league_id=s["league_id"],
            played=s["played"], wins=s["wins"], draws=s["draws"], losses=s["losses"],
            goals_for=s["goals_for"], goals_against=s["goals_against"],
            home_wins=s["home_wins"], home_draws=s["home_draws"], home_losses=s["home_losses"],
            away_wins=s["away_wins"], away_draws=s["away_draws"], away_losses=s["away_losses"],
            clean_sheets=s["clean_sheets"], failed_to_score=s["failed_to_score"],
            form=form,
        )
        db.add(stat)
        created += 1
    
    await db.commit()
    print(f"  写入 {created} 条球队赛季统计")


async def compute_h2h(db):
    """从已有比赛数据计算 HeadToHead"""
    print("\n=== Step 2: 计算历史交锋 ===")
    
    await db.execute(delete(HeadToHead))
    
    result = await db.execute(
        select(Match).where(
            Match.home_score.isnot(None),
            Match.home_team_id.isnot(None),
            Match.away_team_id.isnot(None),
        ).order_by(Match.kickoff_time)
    )
    matches = list(result.scalars().all())
    
    created = 0
    for m in matches:
        h2h = HeadToHead(
            home_team_id=m.home_team_id,
            away_team_id=m.away_team_id,
            match_date=m.kickoff_time,
            competition="",
            home_score=m.home_score,
            away_score=m.away_score,
            sportmonks_fixture_id=m.sportmonks_fixture_id,
        )
        db.add(h2h)
        created += 1
    
    await db.commit()
    print(f"  写入 {created} 条交锋记录")


async def train_models(db):
    """使用完整特征训练模型"""
    print("\n=== Step 3: 训练模型 ===")
    
    result = await db.execute(
        select(Match).where(
            Match.home_score.isnot(None),
            Match.home_team_id.isnot(None),
            Match.away_team_id.isnot(None),
        ).order_by(Match.kickoff_time)
    )
    matches = list(result.scalars().all())
    print(f"  训练样本: {len(matches)} 场")
    
    if len(matches) < 50:
        print("  数据不足")
        return
    
    engineer = FeatureEngineer(db)
    X_list, y_wl_list, y_hcp_list, y_goals_list = [], [], [], []
    skip_count = 0
    
    for i, m in enumerate(matches):
        if (i + 1) % 100 == 0:
            print(f"  提取特征: {i+1}/{len(matches)}")
        try:
            feats = await engineer.extract_features(m.id)
            if feats.empty:
                skip_count += 1
                continue
            X_list.append(feats)
            
            y_wl_list.append(0 if m.home_score > m.away_score else 1 if m.home_score == m.away_score else 2)
            adjusted = m.home_score + (m.handicap_line or 0)
            y_hcp_list.append(0 if adjusted > m.away_score else 1 if adjusted == m.away_score else 2)
            y_goals_list.append(m.home_score + m.away_score)
        except Exception as e:
            skip_count += 1
            continue
    
    if len(X_list) < 50:
        print(f"  有效特征不足: {len(X_list)}")
        return
    
    X = pd.concat(X_list, ignore_index=True)
    # 填充 NaN
    X = X.fillna(0.0)
    
    y_wl = np.array(y_wl_list)
    y_hcp = np.array(y_hcp_list)
    y_goals = np.array(y_goals_list)
    
    print(f"  有效样本: {len(X)} 场, 跳过: {skip_count}")
    print(f"  特征维度: {X.shape[1]}")
    
    # 简单 train/test split
    split = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_wl_train, y_wl_test = y_wl[:split], y_wl[split:]
    y_hcp_train, y_hcp_test = y_hcp[:split], y_hcp[split:]
    y_goals_train, y_goals_test = y_goals[:split], y_goals[split:]
    
    # ── Model A ──
    print("\n  训练 Model A (LightGBM)...")
    model_a = ModelA()
    model_a.train(X_train, y_wl_train, y_hcp_train)
    
    from sklearn.metrics import accuracy_score
    
    y_pred_wl = model_a.model_wl.predict(X_test)
    acc_wl_train = accuracy_score(y_wl_train, model_a.model_wl.predict(X_train))
    acc_wl = accuracy_score(y_wl_test, y_pred_wl)
    
    y_pred_hcp = model_a.model_hcp.predict(X_test)
    acc_hcp_train = accuracy_score(y_hcp_train, model_a.model_hcp.predict(X_train))
    acc_hcp = accuracy_score(y_hcp_test, y_pred_hcp)
    
    print(f"  胜平负: 训练{acc_wl_train:.3f} / 测试{acc_wl:.3f}")
    print(f"  让球:   训练{acc_hcp_train:.3f} / 测试{acc_hcp:.3f}")
    
    # ── Model B ──
    print("\n  训练 Model B (Poisson)...")
    mask = y_goals_train > 0
    X_train_b, y_train_b = X_train[mask], y_goals_train[mask]
    X_test_b, y_test_b = X_test, y_goals_test
    
    model_b = ModelB()
    model_b.train(X_train_b, y_train_b)
    
    y_pred_goals = model_b.model.predict(X_test_b)
    mae = np.abs(y_test_b - y_pred_goals).mean()
    print(f"  进球预测 MAE: {mae:.3f}")
    
    # ── 类别分布 ──
    from collections import Counter
    wl_dist = Counter(y_wl)
    print(f"\n  标签分布: 主胜={wl_dist[0]}, 平局={wl_dist[1]}, 客胜={wl_dist[2]}")
    print(f"  基线准确率 (猜主胜): {wl_dist[0]/len(y_wl):.3f}")
    
    # ── 持久化 ──
    model_dir = "backend/models"
    os.makedirs(model_dir, exist_ok=True)
    
    joblib.dump(model_a.model_wl, os.path.join(model_dir, "model_a_wl.pkl"))
    joblib.dump(model_a.model_hcp, os.path.join(model_dir, "model_a_hcp.pkl"))
    joblib.dump(model_b.model, os.path.join(model_dir, "model_b.pkl"))
    
    version = datetime.now().strftime("%Y%m%d-%H%M")
    meta = {
        "version": version,
        "samples": len(X),
        "features": list(X.columns),
        "acc_wl_train": round(float(acc_wl_train), 4),
        "acc_wl_test": round(float(acc_wl), 4),
        "acc_hcp_train": round(float(acc_hcp_train), 4),
        "acc_hcp_test": round(float(acc_hcp), 4),
        "mae_goals": round(float(mae), 4),
        "wl_baseline": round(wl_dist[0]/len(y_wl), 4),
        "trained_at": datetime.now().isoformat(),
    }
    joblib.dump(meta, os.path.join(model_dir, f"model_meta_{version}.pkl"))
    
    print(f"\n  模型已保存: backend/models/")
    print(f"  版本: {version}")
    print(f"  胜平负: 训练{acc_wl_train:.1%} / 测试{acc_wl:.1%} (基线{wl_dist[0]/len(y_wl):.1%})")
    print(f"  让球:   训练{acc_hcp_train:.1%} / 测试{acc_hcp:.1%}")
    print(f"  进球MAE: {mae:.2f}")


async def main():
    async with async_session() as db:
        await compute_team_stats(db)
        await compute_h2h(db)
        await train_models(db)

if __name__ == "__main__":
    asyncio.run(main())
