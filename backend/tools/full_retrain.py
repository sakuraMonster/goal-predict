"""修正全部联赛 league_id + 重训练 + 批量预测"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

import numpy as np, pandas as pd, joblib, lightgbm as lgb
from sklearn.preprocessing import StandardScaler
from datetime import datetime
from collections import Counter
from app.db.database import async_session
from app.db.models import Match, Team, League, TeamSeasonStats, HeadToHead
from app.collector.sportmonks.client import SportMonksClient
from app.predictor.features_a import FeatureEngineerA
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB
from sqlalchemy import select, delete, update, func

# 联赛配置: local_id → (sm_league_id, 名称)
LEAGUES = {
    6:  (1034, "韩K"),
    7:  (968,  "日职联"),
    9:  (573,  "瑞典超"),
    10: (292,  "芬超"),
    11: (444,  "挪超"),
    12: (779,  "美职联"),
    13: (648,  "巴西甲"),
}

MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)


async def fix_all_leagues():
    """为每个联赛: 从 API 找球队 → 匹配本地 → 更新 league_id"""
    c = SportMonksClient()
    try:
        async with async_session() as db:
            for local_id, (sm_id, name) in LEAGUES.items():
                print(f"\n[{name}]")
                
                # 1. 采样球队
                team_sm_ids = set()
                sample_dates = ["2025-04-12", "2025-07-20", "2025-10-05", "2025-03-01", "2025-08-16"]
                for dt in sample_dates:
                    try:
                        fx = await c.get_fixtures_by_date(dt, "participants")
                        for f in fx:
                            if f.get("league_id") == sm_id:
                                for p in f.get("participants", []):
                                    team_sm_ids.add(p["id"])
                    except: pass
                
                if not team_sm_ids:
                    # 韩K/巴西甲可能日期不同，试试不同月份
                    for dt in ["2025-02-22","2025-05-10","2025-06-15","2025-09-20","2025-11-08"]:
                        try:
                            fx = await c.get_fixtures_by_date(dt, "participants")
                            for f in fx:
                                if f.get("league_id") == sm_id:
                                    for p in f.get("participants", []):
                                        team_sm_ids.add(p["id"])
                        except: pass
                
                if not team_sm_ids:
                    print(f"  未找到球队（API无此联赛数据）")
                    continue
                
                print(f"  API找到 {len(team_sm_ids)} 支球队")
                
                # 2. 匹配本地
                teams = await db.execute(select(Team).where(Team.sportmonks_id.in_(team_sm_ids)))
                local_ids = [t.id for t in teams.scalars().all()]
                print(f"  本地匹配: {len(local_ids)} 支")
                
                if not local_ids:
                    continue
                
                # 3. 更新 league_id：仅当双方球队都属于该联赛
                #    避免将亚冠等跨联赛比赛误标（如韩K球队 vs 非韩K球队）
                local_set = set(local_ids)
                matches_to_fix = []
                all_matches = await db.execute(
                    select(Match).where(Match.league_id == 1)
                )
                for m in all_matches.scalars().all():
                    if m.home_team_id in local_set and m.away_team_id in local_set:
                        matches_to_fix.append(m.id)
                
                if matches_to_fix:
                    await db.execute(
                        update(Match).where(Match.id.in_(matches_to_fix)).values(league_id=local_id)
                    )
                    await db.commit()
                    print(f"  双方同联赛修正: {len(matches_to_fix)} 场")
                else:
                    print(f"  双方同联赛修正: 0 场")
                
                # 4. 更新联赛 sportmonks_id
                await db.execute(update(League).where(League.id==local_id).values(sportmonks_id=sm_id))
                await db.commit()
                
                total = await db.execute(select(func.count()).select_from(Match).where(Match.league_id==local_id))
                scored = await db.execute(select(func.count()).select_from(Match).where(Match.league_id==local_id, Match.home_score.isnot(None)))
                print(f"  修正后: 总计 {total.scalar()} 场, 有比分 {scored.scalar()} 场")
    finally:
        await c.close()


async def retrain():
    """全量重训练 + 预测"""
    # 备份旧模型
    import shutil
    for f in ["model_a_wl.pkl", "model_a_hcp.pkl", "model_b.pkl", "model_b_scaler.pkl"]:
        src = os.path.join(MODEL_DIR, f)
        if os.path.exists(src):
            backup = src + ".bak"
            shutil.copy2(src, backup)
            print(f"已备份: {src} → {backup}")
    
    async with async_session() as db:
        # 保存现有 recent_matches（管道拉取，重训练需合并回来）
        old_recent = {}
        r_result = await db.execute(select(TeamSeasonStats.team_id, TeamSeasonStats.recent_matches))
        for row in r_result:
            if row.recent_matches:
                old_recent[row.team_id] = row.recent_matches

        # 保存现有 H2H（管道拉取，可能含本地 match 表没有的记录）
        old_h2h = []
        h2h_result = await db.execute(select(HeadToHead))
        for row in h2h_result.scalars().all():
            old_h2h.append({
                "home_team_id": row.home_team_id,
                "away_team_id": row.away_team_id,
                "match_date": row.match_date,
                "home_score": row.home_score,
                "away_score": row.away_score,
                "sportmonks_fixture_id": row.sportmonks_fixture_id,
                "home_stats": row.home_stats,  # V4.1: 保留 xG/射门/控球等过程数据
                "away_stats": row.away_stats,
            })

        # 清旧特征
        await db.execute(delete(TeamSeasonStats))
        await db.execute(delete(HeadToHead))
        
        result = await db.execute(
            select(Match).where(
                Match.home_score.isnot(None),
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
            ).order_by(Match.kickoff_time))
        matches = list(result.scalars().all())
        
        lc = Counter(m.league_id for m in matches)
        print(f"\n=== 全量样本 {len(matches)} 场 ===")
        for lid in sorted(lc): print(f"  league_id={lid}: {lc[lid]} 场")
        
        # 计算 TeamSeasonStats
        sdict = {}
        for m in matches:
            sea = str(m.kickoff_time.year)
            for tid, ih, gf, ga in [(m.home_team_id,True,m.home_score,m.away_score),(m.away_team_id,False,m.away_score,m.home_score)]:
                k = (tid, sea)
                if k not in sdict:
                    sdict[k] = {"team_id":tid,"season":sea,"league_id":m.league_id,"p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,"hw":0,"hd":0,"hl":0,"aw":0,"ad":0,"al":0,"cs":0,"fs":0,"r":[]}
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
        
        # 合并回外部 H2H（管道拉取但本地无对应 match 记录的数据）
        if old_h2h:
            existing_set = set()
            eh_result = await db.execute(select(HeadToHead.home_team_id, HeadToHead.away_team_id, HeadToHead.match_date))
            for row in eh_result:
                existing_set.add((row[0], row[1], row[2].strftime("%Y-%m-%d") if row[2] else ""))
            
            restored = 0
            for h in old_h2h:
                key = (h["home_team_id"], h["away_team_id"],
                       h["match_date"].strftime("%Y-%m-%d") if h["match_date"] else "")
                if key not in existing_set:
                    db.add(HeadToHead(**h))
                    restored += 1
            if restored:
                await db.commit()
                print(f"  外部 H2H 已合并 {restored} 条")
        
        # V4.1: 将旧 H2H 的内容数据（xG/射门/控球）合并回新记录
        if old_h2h:
            h2h_all = await db.execute(select(HeadToHead))
            updated_content = 0
            for h2h_row in h2h_all.scalars().all():
                # 查找匹配的旧记录
                for old in old_h2h:
                    if (old["home_team_id"] == h2h_row.home_team_id and 
                        old["away_team_id"] == h2h_row.away_team_id and
                        old["match_date"].strftime("%Y-%m-%d") == h2h_row.match_date.strftime("%Y-%m-%d") if h2h_row.match_date else False):
                        if old.get("home_stats") and not h2h_row.home_stats:
                            h2h_row.home_stats = old["home_stats"]
                            updated_content += 1
                        if old.get("away_stats") and not h2h_row.away_stats:
                            h2h_row.away_stats = old["away_stats"]
                        break
            if updated_content:
                await db.commit()
                print(f"  H2H 内容数据已合并 {updated_content} 条")
        
        # 合并回 recent_matches（管道阶段2数据，用于 V3 近期状态特征）
        if old_recent:
            ts_result = await db.execute(select(TeamSeasonStats))
            for ts in ts_result.scalars():
                if ts.team_id in old_recent:
                    ts.recent_matches = old_recent[ts.team_id]
            await db.commit()
            print(f"  recent_matches 已合并 {len(old_recent)} 支球队")
        
        # 提取特征
        print("\n提取特征...")
        eng = FeatureEngineerA(db)
        Xl, yw, yg = [], [], []
        Xh, yh = [], []  # 让球模型专用：仅含真实让球线的比赛
        for i, m in enumerate(matches):
            if (i+1)%1000==0: print(f"  {i+1}/{len(matches)}")
            try:
                f = await eng.extract_features(m.id)
                if f.empty:
                    raise ValueError(f"match_id={m.id} 特征提取返回空 DataFrame")
                Xl.append(f)
                yw.append(0 if m.home_score>m.away_score else 1 if m.home_score==m.away_score else 2)
                yg.append(m.home_score+m.away_score)
                # 让球标签：仅当让球线非零非空时才纳入训练（否则与胜平负标签相同，无学习价值）
                if m.handicap_line and m.handicap_line != 0:
                    adj = m.home_score + m.handicap_line
                    Xh.append(f)
                    yh.append(0 if adj>m.away_score else 1 if adj==m.away_score else 2)
            except Exception as e:
                print(f"\n  ⛔ 特征提取失败 match_id={m.id} ({m.home_team_name} vs {m.away_team_name}): {e}")
                raise  # 立即中断训练
        
        X = pd.concat(Xl).fillna(0.0)
        yw = np.array(yw); yg = np.array(yg)
        sp = int(len(X)*0.8)
        
        # 特征标准化：消除量级差异（控球率~50 vs 胜率~0.5），让 Poisson 回归系数公平竞争
        scaler = StandardScaler()
        cols = X.columns.tolist()
        X_train_scaled = scaler.fit_transform(X[:sp])
        X_test_scaled = scaler.transform(X[sp:])
        X = pd.DataFrame(np.vstack([X_train_scaled, X_test_scaled]), columns=cols)
        print(f"特征已标准化 (StandardScaler)，训练集 {sp} 条，测试集 {len(X)-sp} 条")
        
        wc = Counter(yw)
        print(f"有效: {len(X)}, 维度: {X.shape[1]}, 主={wc[0]}平={wc[1]}客={wc[2]}基线={wc[0]/len(yw):.3f}")
        print(f"让球样本: {len(Xh)} (胜平负样本: {len(X)})")
        
        # ── L3 偏离度特征：市场隐含概率 vs 纯数据模型概率 ──
        # 核心思路：市场赔率反映博彩公司综合判断，纯数据模型反映历史规律，
        # 两者的偏离度揭示市场是否存在系统性偏差，是让球预测的关键增量信号。
        # 
        # 注意：仅用于让球模型（Xh），胜平负模型保持原有特征集，
        # 避免 base model 的过拟合偏差污染 1X2 预测准确率。
        from sklearn.metrics import accuracy_score
        odds_cols = [c for c in X.columns if c.startswith("odds_")]
        base_cols = [c for c in X.columns if c not in odds_cols]
        mp_cols = ["odds_market_home_prob", "odds_market_draw_prob", "odds_market_away_prob"]
        
        # Model A - 胜平负模型（不含偏离度，避免噪声）
        print("\nModel A...")
        ma = ModelA()
        ma.model_wl = lgb.LGBMClassifier(
            objective="multiclass", num_class=3,
            n_estimators=200, learning_rate=0.05, max_depth=6,
            random_state=42, verbose=-1
        )
        ma.model_wl.fit(X[:sp], yw[:sp])
        
        # 让球模型：使用 L3 偏离度增强
        if len(Xh) >= 100:
            print("\n计算 L3 偏离度特征（让球模型专用）...")
            Xh_pd = pd.concat(Xh).fillna(0.0)
            
            # Stage 1: 训练轻量级纯数据模型（低复杂度防过拟合）
            base_model = lgb.LGBMClassifier(
                objective="multiclass", num_class=3,
                n_estimators=60, learning_rate=0.03, max_depth=4,
                min_child_samples=50, reg_alpha=0.1, reg_lambda=0.1,
                random_state=42, verbose=-1
            )
            base_model.fit(Xh_pd[base_cols], yh)  # 让球全集训练base（用于特征工程）
            bh_probs = base_model.predict_proba(Xh_pd[base_cols])
            mh_probs = Xh_pd[mp_cols].values
            dh = mh_probs - bh_probs
            
            Xh_pd["hcp_dev_home"] = dh[:, 0]
            Xh_pd["hcp_dev_draw"] = dh[:, 1]
            Xh_pd["hcp_dev_away"] = dh[:, 2]
            Xh_pd["hcp_dev_magnitude"] = np.abs(dh).sum(axis=1) / 3
            print(f"  偏离度特征已添加 → 让球模型维度: {Xh_pd.shape[1]}")
            
            yh_arr = np.array(yh)
            sh = int(len(Xh_pd) * 0.8)
            ma.model_hcp = lgb.LGBMClassifier(
                objective="multiclass", num_class=3,
                n_estimators=200, learning_rate=0.05, max_depth=6,
                random_state=42, verbose=-1
            )
            ma.model_hcp.fit(Xh_pd[:sh], yh_arr[:sh])
            ah = accuracy_score(yh_arr[sh:], ma.model_hcp.predict(Xh_pd[sh:]))
        else:
            ma.model_hcp = None
            ah = 0.0
        
        aw = accuracy_score(yw[sp:], ma.model_wl.predict(X[sp:]))
        print(f"  胜平负: {aw:.4f} 让球: {ah:.4f}")
        
        # Model B
        print("Model B...")
        mk = yg[:sp] > 0
        mb = ModelB(); mb.train(X[:sp][mk], yg[:sp][mk])
        mae = np.abs(yg[sp:] - mb.model.predict(X[sp:])).mean()
        print(f"  进球MAE: {mae:.4f}")
        
        joblib.dump(ma.model_wl, os.path.join(MODEL_DIR,"model_a_wl.pkl"))
        if ma.model_hcp:
            joblib.dump(ma.model_hcp, os.path.join(MODEL_DIR,"model_a_hcp.pkl"))
        else:
            # 让球样本不足时，复制胜平负模型作为 fallback
            import shutil
            shutil.copy2(os.path.join(MODEL_DIR,"model_a_wl.pkl"), os.path.join(MODEL_DIR,"model_a_hcp.pkl"))
            print("  让球模型: 样本不足，使用胜平负模型替代")
        joblib.dump(mb.model, os.path.join(MODEL_DIR,"model_b.pkl"))
        joblib.dump(scaler, os.path.join(MODEL_DIR,"model_b_scaler.pkl"))
        
        # V4.1: 批量预测移至独立脚本执行（避免 SM API 调用拖慢训练）
        # 训练完成后运行 _batch_predict_fast.py 单独做预测
        
        # 复制模型到上级目录
        import shutil
        for f in ["model_a_wl.pkl","model_a_hcp.pkl","model_b.pkl","model_b_scaler.pkl"]:
            try: shutil.copy2(os.path.join(MODEL_DIR,f), os.path.join("..","models",f))
            except: pass
        
        print(f"\n完成! v{datetime.now().strftime('%Y%m%d-%H%M')} 胜平负={aw:.4f} MAE={mae:.4f}")


async def main():
    # V4.1: 跳过 fix_all_leagues（联赛数据已修正，避免 SM API 调用）
    # await fix_all_leagues()
    print("联赛修正已跳过（数据已就绪）")
    await retrain()

if __name__ == "__main__":
    asyncio.run(main())
