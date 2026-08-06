"""08-04 干净重预测：剔除08-04比赛本身的特征数据后运行 Model B"""
import asyncio
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Prediction, TeamSeasonStats
from app.predictor.pipeline import PredictionPipeline

async def main():
    async with async_session() as db:
        start = datetime(2026, 8, 4, 12, 0, 0)
        end = datetime(2026, 8, 5, 12, 0, 0)

        r = await db.execute(
            select(Match).where(Match.kickoff_time >= start, Match.kickoff_time < end)
            .order_by(Match.kickoff_time)
        )
        matches = list(r.scalars().all())
        print(f'08-04 共 {len(matches)} 场比赛')

        # 收集涉及球队
        team_ids = set()
        for m in matches:
            if m.home_team_id:
                team_ids.add(m.home_team_id)
            if m.away_team_id:
                team_ids.add(m.away_team_id)

        # 获取球队统计
        r2 = await db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.team_id.in_(team_ids))
            .order_by(TeamSeasonStats.played.desc())
        )
        all_stats = list(r2.scalars().all())
        stat_map = {}
        for s in all_stats:
            if s.team_id not in stat_map or s.played > stat_map[s.team_id].played:
                stat_map[s.team_id] = s

        # 备份
        backups = {}
        for tid, s in stat_map.items():
            backups[tid] = {k: getattr(s, k) for k in ['played', 'wins', 'draws', 'losses', 'goals_for', 'goals_against']}
            backups[tid]['recent_matches'] = list(s.recent_matches or [])

        # 剔除 08-04 比赛
        removed = 0
        for m in matches:
            if m.home_score is None or m.away_score is None:
                continue
            for tid, gf, ga in [(m.home_team_id, m.home_score, m.away_score), (m.away_team_id, m.away_score, m.home_score)]:
                if tid not in stat_map:
                    continue
                s = stat_map[tid]
                s.played = max(0, (s.played or 0) - 1)
                s.goals_for = max(0, (s.goals_for or 0) - gf)
                s.goals_against = max(0, (s.goals_against or 0) - ga)
                if gf > ga:
                    s.wins = max(0, (s.wins or 0) - 1)
                elif gf == ga:
                    s.draws = max(0, (s.draws or 0) - 1)
                else:
                    s.losses = max(0, (s.losses or 0) - 1)
                removed += 1
        await db.commit()
        print(f'剔除 {removed} 条统计记录')

        try:
            pipeline = PredictionPipeline(db)
            model_version = datetime.now().strftime('%Y%m%d-%H%M')
            updated = 0

            for m in matches:
                try:
                    pred_result = await pipeline.predict(m.id)
                except Exception as e:
                    print(f'  FAIL {m.id}: {e}')
                    continue

                existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
                pred = existing.scalar_one_or_none()
                if not pred:
                    print(f'  SKIP {m.id}: no prediction')
                    continue

                pred.expected_goals = pred_result['expected_goals']
                pred.over_2_5_prob = pred_result['over_2_5_prob']
                pred.goal_distribution = pred_result['goal_distribution']
                pred.snap_top2 = pred_result['snap_top2']
                pred.score_top5_json = pred_result['score_top5_json']
                pred.summary_text = pred_result.get('summary_text', '')
                pred.key_factors = pred_result.get('key_factors', '')
                pred.model_version = model_version
                if pred.actual_total_goals is not None and pred.snap_top2:
                    pred.result_goals = 1 if pred.actual_total_goals in pred.snap_top2 else -1
                updated += 1
                print(f'  OK {m.id} {m.home_team_name} vs {m.away_team_name}: eg={pred_result["expected_goals"]:.2f} snap={pred_result["snap_top2"]}')

            await db.commit()
            print(f'\n预测完成: {updated} 场, version={model_version}')
        finally:
            # 恢复统计
            for tid, bk in backups.items():
                s = stat_map[tid]
                for k in ['played', 'wins', 'draws', 'losses', 'goals_for', 'goals_against']:
                    setattr(s, k, bk[k])
                s.recent_matches = bk['recent_matches']
            await db.commit()
            print('统计已恢复')

asyncio.run(main())
