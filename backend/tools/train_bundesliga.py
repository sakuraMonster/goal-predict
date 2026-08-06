"""完整Pipeline: 拉取德甲数据 + 重算特征 + 重训练 + 批量预测"""
import asyncio, os, sys, calendar
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
import joblib
from datetime import datetime
from collections import defaultdict

from app.db.database import async_session
from app.db.models import Match, Team, League, TeamSeasonStats, HeadToHead, Prediction
from app.collector.sportmonks.client import SportMonksClient
from app.predictor.features import FeatureEngineer
from app.predictor.models.model_a import ModelA
from app.predictor.models.model_b import ModelB
from sqlalchemy import select, delete, update

# ── 配置 ──
BUNDESLIGA_SM_ID = 82
BUNDESLIGA_SEASON_ID = 25646
BUNDESLIGA_LOCAL_ID = 3
MODEL_DIR = "models"
os.makedirs(MODEL_DIR, exist_ok=True)


async def sync_league(db):
    """更新德甲 SportMonks ID"""
    await db.execute(update(League).where(League.id == BUNDESLIGA_LOCAL_ID).values(sportmonks_id=BUNDESLIGA_SM_ID))
    await db.commit()
    print(f"德甲联赛 sportmonks_id → {BUNDESLIGA_SM_ID}")


async def sync_teams_and_fixtures(db, client, league_local_id, sm_league_id, season_id):
    """拉取某联赛的球队和比赛数据"""
    label = {1: "英超", 3: "德甲"}.get(league_local_id, f"联赛{league_local_id}")

    # ── 球队 ──
    print(f"  [{label}] 拉取球队...")
    teams_data = await client.get_teams_by_season(season_id)
    team_map = {}
    for p in teams_data:
        sm_id = p["id"]
        existing = await db.execute(select(Team).where(Team.sportmonks_id == sm_id))
        t = existing.scalar_one_or_none()
        if not t:
            t = Team(
                sportmonks_id=sm_id, league_id=league_local_id,
                name_zh=p.get("name", ""), name_en=p.get("name", ""),
                short_en=p.get("short_code", p.get("name", "")[:3]),
                logo_url=p.get("image_path", ""),
            )
            db.add(t)
            await db.flush()
        team_map[sm_id] = t
    print(f"    球队: {len(team_map)}")

    # ── 比赛（按月拉取） ──
    print(f"  [{label}] 拉取比赛...")
    months = [(2025, m) for m in range(8, 13)] + [(2026, m) for m in range(1, 6)]
    total_new = 0

    for yr, mo in months:
        from_date = f"{yr}-{mo:02d}-01"
        last_day = calendar.monthrange(yr, mo)[1]
        to_date = f"{yr}-{mo:02d}-{last_day}"
        try:
            fixtures = await client.get_fixtures_between(from_date, to_date, "participants;scores")
        except:
            continue

        for f in fixtures:
            fixture_id = f["id"]
            existing = await db.execute(select(Match).where(Match.sportmonks_fixture_id == fixture_id))
            if existing.scalar_one_or_none():
                continue

            participants = f.get("participants", [])
            home_p = next((p for p in participants if p.get("meta", {}).get("location") == "home"), None)
            away_p = next((p for p in participants if p.get("meta", {}).get("location") == "away"), None)
            if not home_p or not away_p:
                continue

            h_id, a_id = home_p["id"], away_p["id"]

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
                league_id=league_local_id,
                home_team_id=team_map[h_id].id if h_id in team_map else None,
                away_team_id=team_map[a_id].id if a_id in team_map else None,
                home_team_name=home_p.get("name", "?"),
                away_team_name=away_p.get("name", "?"),
                kickoff_time=kickoff,
                home_score=h_score, away_score=a_score,
                status="finished" if h_score is not None else "scheduled",
            )
            db.add(m)
            total_new += 1

        if total_new > 0:
            await db.commit()

    print(f"    新增比赛: {total_new}")


async def compute_all_stats(db):
    """重算所有比赛的 TeamSeasonStats 和 HeadToHead"""
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

    # ── TeamSeasonStats ──
    stats_map = {}
    for m in matches:
        season = str(m.kickoff_time.year) if m.kickoff_time else "2025"
        for tid, is_home, gf, ga, opp_id in [
            (m.home_team_id, True, m.home_score, m.away_score, m.away_team_id),
            (m.away_team_id, False, m.away_score, m.home_score, m.home_team_id),
        ]:
            key = (tid, season)
            if key not in stats_map:
                stats_map[key] = {"team_id": tid, "season": season, "league_id": m.league_id,
                    "played": 0, "wins": 0, "draws": 0, "losses": 0,
                    "goals_for": 0, "goals_against": 0,
                    "home_wins": 0, "home_draws": 0, "home_losses": 0,
                    "away_wins": 0, "away_draws": 0, "away_losses": 0,
                    "clean_sheets": 0, "failed_to_score": 0, "results": []}

            s = stats_map[key]; s["played"] += 1
            s["goals_for"] += gf; s["goals_against"] += ga
            if gf > ga: s["wins"] += 1; s["results"].append("W")
            elif gf == ga: s["draws"] += 1; s["results"].append("D")
            else: s["losses"] += 1; s["results"].append("L")
            if is_home:
                if gf > ga: s["home_wins"] += 1
                elif gf == ga: s["home_draws"] += 1
                else: s["home_losses"] += 1
            else:
                if gf > ga: s["away_wins"] += 1
                elif gf == ga: s["away_draws"] += 1
                else: s["away_losses"] += 1
            if ga == 0: s["clean_sheets"] += 1
            if gf == 0: s["failed_to_score"] += 1

    for (tid, season), s in stats_map.items():
        stat = TeamSeasonStats(
            team_id=tid, season=season, league_id=s["league_id"],
            played=s["played"], wins=s["wins"], draws=s["draws"], losses=s["losses"],
            goals_for=s["goals_for"], goals_against=s["goals_against"],
            home_wins=s["home_wins"], home_draws=s["home_draws"], home_losses=s["home_losses"],
            away_wins=s["away_wins"], away_draws=s["away_draws"], away_losses=s["away_losses"],
            clean_sheets=s["clean_sheets"], failed_to_score=s["failed_to_score"],
            form="".join(s["results"][-5:]),
        )
        db.add(stat)
    print(f"  TeamSeasonStats: {len(stats_map)} 条")

    # ── HeadToHead ──
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
    """训练模型并批量预测"""
    result = await db.execute(
        select(Match).where(
            Match.home_score.isnot(None),
            Match.home_team_id.isnot(None),
            Match.away_team_id.isnot(None),
        ).order_by(Match.kickoff_time)
    )
    matches = list(result.scalars().all())
    print(f"\n训练样本: {len(matches)} 场")

    engineer = FeatureEngineer(db)
    X_list, y_wl_list, y_hcp_list, y_goals_list = [], [], [], []

    for i, m in enumerate(matches):
        if (i + 1) % 300 == 0:
            print(f"  提取特征: {i+1}/{len(matches)}")
        try:
            feats = await engineer.extract_features(m.id)
            if feats.empty: continue
            X_list.append(feats)
            y_wl_list.append(0 if m.home_score > m.away_score else 1 if m.home_score == m.away_score else 2)
            adj = m.home_score + (m.handicap_line or 0)
            y_hcp_list.append(0 if adj > m.away_score else 1 if adj == m.away_score else 2)
            y_goals_list.append(m.home_score + m.away_score)
        except:
            continue

    X = pd.concat(X_list, ignore_index=True).fillna(0.0)
    y_wl = np.array(y_wl_list); y_hcp = np.array(y_hcp_list); y_goals = np.array(y_goals_list)
    print(f"  有效样本: {len(X)} 场, 特征维度: {X.shape[1]}")

    from collections import Counter
    wl_dist = Counter(y_wl)
    print(f"  标签分布: 主胜={wl_dist[0]} 平局={wl_dist[1]} 客胜={wl_dist[2]}")

    split = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    yt_wl, ye_wl = y_wl[:split], y_wl[split:]
    yt_hcp, ye_hcp = y_hcp[:split], y_hcp[split:]
    yt_g, ye_g = y_goals[:split], y_goals[split:]

    # ── Model A ──
    print("\n训练 Model A...")
    ma = ModelA()
    ma.train(X_train, yt_wl, yt_hcp)
    from sklearn.metrics import accuracy_score
    acc_wl = accuracy_score(ye_wl, ma.model_wl.predict(X_test))
    acc_hcp = accuracy_score(ye_hcp, ma.model_hcp.predict(X_test))
    print(f"  胜平负: {acc_wl:.3f}  让球: {acc_hcp:.3f}  (基线 {wl_dist[0]/len(y_wl):.3f})")

    # ── Model B ──
    print("训练 Model B...")
    mask = yt_g > 0
    mb = ModelB()
    mb.train(X_train[mask], yt_g[mask])
    yp_g = mb.model.predict(X_test)
    mae = np.abs(ye_g - yp_g).mean()
    print(f"  进球MAE: {mae:.3f}")

    # ── 持久化 ──
    joblib.dump(ma.model_wl, os.path.join(MODEL_DIR, "model_a_wl.pkl"))
    joblib.dump(ma.model_hcp, os.path.join(MODEL_DIR, "model_a_hcp.pkl"))
    joblib.dump(mb.model, os.path.join(MODEL_DIR, "model_b.pkl"))

    version = datetime.now().strftime("%Y%m%d-%H%M")
    print(f"\n模型已保存 v{version}")

    # ── 批量预测 ──
    print("\n批量预测...")
    all_matches = await db.execute(
        select(Match).where(Match.home_team_id.isnot(None), Match.away_team_id.isnot(None))
    )
    pipeline = __import__('app.predictor.pipeline', fromlist=['PredictionPipeline']).PredictionPipeline(db)

    new, upd = 0, 0
    for i, m in enumerate(all_matches.scalars().all()):
        if (i + 1) % 500 == 0:
            print(f"  预测: {i+1}")
        try:
            r = await pipeline.predict(m.id)
        except:
            continue
        existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
        pred = existing.scalar_one_or_none()
        if pred:
            pred.home_prob = r["home_prob"]; pred.draw_prob = r["draw_prob"]; pred.away_prob = r["away_prob"]
            pred.handicap_home_prob = r["handicap_home_prob"]; pred.handicap_draw_prob = r["handicap_draw_prob"]; pred.handicap_away_prob = r["handicap_away_prob"]
            pred.expected_goals = r["expected_goals"]; pred.over_2_5_prob = r["over_2_5_prob"]
            pred.goal_distribution = r["goal_distribution"]; pred.score_top5_json = r["score_top5_json"]
            pred.confidence_level = r["confidence_level"]; pred.is_cold_match = r["is_cold_match"]
            pred.summary_text = r["summary_text"]; pred.key_factors = r.get("key_factors", ""); pred.model_version = version
            upd += 1
        else:
            db.add(Prediction(match_id=m.id, model_version=version, **{k: v for k, v in r.items() if k in [
                "home_prob","draw_prob","away_prob","handicap_home_prob","handicap_draw_prob","handicap_away_prob",
                "expected_goals","over_2_5_prob","goal_distribution","score_top5_json",
                "confidence_level","is_cold_match","summary_text","key_factors"
            ]}))
            new += 1
        if (i + 1) % 100 == 0:
            await db.commit()
    await db.commit()
    print(f"  预测: 新增 {new}, 更新 {upd}")


async def main():
    client = SportMonksClient()
    try:
        async with async_session() as db:
            print("=" * 50)
            print("Step 1: 更新德甲联赛ID")
            await sync_league(db)

            print("\nStep 2: 同步德甲球队和比赛")
            await sync_teams_and_fixtures(db, client, BUNDESLIGA_LOCAL_ID, BUNDESLIGA_SM_ID, BUNDESLIGA_SEASON_ID)

            print("\nStep 3: 重算特征")
            await compute_all_stats(db)

            print("\nStep 4: 训练+预测")
            await train_and_predict(db)
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
