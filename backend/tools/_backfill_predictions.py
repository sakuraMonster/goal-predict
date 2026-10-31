"""为指定历史比赛日补生成预测（Model A/B/C/D），回写 predictions 表。

背景：predict 定时任务只预测未来 48h 的场次，历史比赛日的预测从未生成过，
导致赛果回写（挂 Prediction 表）匹配不到记录。本脚本对历史窗口补跑预测。

默认补齐 [2026-10-22 12:00, 2026-10-24 12:00)（10-22 与 10-23 两个竞彩比赛日）。
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

_CONTAINER_APP_ROOT = "/app"
if os.path.isdir(os.path.join(_CONTAINER_APP_ROOT, "app")):
    sys.path.insert(0, _CONTAINER_APP_ROOT)

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.pipeline import PredictionPipeline

# kickoff_time 存北京 naive；比赛日窗口 [START 12:00, END 12:00)
# 用法：python _backfill_predictions.py [START日期] [END日期]
#   例：python _backfill_predictions.py 2026-10-24             → 补 10-24 比赛日（10-24 12:00 ~ 10-25 12:00）
#       python _backfill_predictions.py 2026-10-22 2026-10-24 → 补 10-22、10-23 两个比赛日
def _parse_day(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(hour=12, minute=0, second=0, microsecond=0)

if len(sys.argv) > 1:
    START = _parse_day(sys.argv[1])
    END = _parse_day(sys.argv[2]) if len(sys.argv) > 2 else START + timedelta(days=1)
else:
    START = datetime(2026, 10, 22, 12, 0, 0)
    END = datetime(2026, 10, 24, 12, 0, 0)
print(f"窗口: [{START}, {END})", flush=True)


async def main():
    async with async_session() as db:
        result = await db.execute(
            select(Match).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
                Match.kickoff_time >= START,
                Match.kickoff_time < END,
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        print(f"待预测比赛 {len(matches)} 场", flush=True)

        pipeline = PredictionPipeline(db)
        version = datetime.now().strftime("%Y%m%d-%H%M")
        created = updated = failed = 0

        for i, m in enumerate(matches):
            if (i + 1) % 5 == 0:
                print(f"  进度 {i + 1}/{len(matches)}", flush=True)
            try:
                pred_result = await pipeline.predict(m.id)
            except Exception as e:
                failed += 1
                print(f"  [FAIL] match_id={m.id}: {e}", flush=True)
                continue

            existing = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = existing.scalar_one_or_none()

            if pred:
                pred.home_prob = pred_result["home_prob"]
                pred.draw_prob = pred_result["draw_prob"]
                pred.away_prob = pred_result["away_prob"]
                pred.handicap_home_prob = pred_result["handicap_home_prob"]
                pred.handicap_draw_prob = pred_result["handicap_draw_prob"]
                pred.handicap_away_prob = pred_result["handicap_away_prob"]
                pred.expected_goals = pred_result["expected_goals"]
                pred.over_2_5_prob = pred_result["over_2_5_prob"]
                pred.goal_distribution = pred_result["goal_distribution"]
                pred.snap_top2 = pred_result["snap_top2"]
                pred.expected_goals_c = pred_result.get("expected_goals_c")
                pred.expected_goals_d = pred_result.get("expected_goals_d")
                pred.snap_top2_c = pred_result.get("snap_top2_c")
                pred.snap_top2_d = pred_result.get("snap_top2_d")
                pred.score_top5_json = pred_result["score_top5_json"]
                pred.confidence_level = pred_result["confidence_level"]
                pred.is_cold_match = pred_result["is_cold_match"]
                pred.cold_correction = pred_result.get("cold_correction")
                pred.summary_text = pred_result["summary_text"]
                pred.key_factors = pred_result.get("key_factors", "")
                pred.model_version = version
                pred.kickoff_time = m.kickoff_time
                pred.league_id = m.league_id
                updated += 1
            else:
                db.add(Prediction(
                    match_id=m.id, model_version=version,
                    home_prob=pred_result["home_prob"],
                    draw_prob=pred_result["draw_prob"],
                    away_prob=pred_result["away_prob"],
                    handicap_home_prob=pred_result["handicap_home_prob"],
                    handicap_draw_prob=pred_result["handicap_draw_prob"],
                    handicap_away_prob=pred_result["handicap_away_prob"],
                    expected_goals=pred_result["expected_goals"],
                    over_2_5_prob=pred_result["over_2_5_prob"],
                    goal_distribution=pred_result["goal_distribution"],
                    snap_top2=pred_result["snap_top2"],
                    expected_goals_c=pred_result.get("expected_goals_c"),
                    expected_goals_d=pred_result.get("expected_goals_d"),
                    snap_top2_c=pred_result.get("snap_top2_c"),
                    snap_top2_d=pred_result.get("snap_top2_d"),
                    score_top5_json=pred_result["score_top5_json"],
                    confidence_level=pred_result["confidence_level"],
                    is_cold_match=pred_result["is_cold_match"],
                    cold_correction=pred_result.get("cold_correction"),
                    summary_text=pred_result["summary_text"],
                    key_factors=pred_result.get("key_factors", ""),
                    kickoff_time=m.kickoff_time,
                    league_id=m.league_id,
                ))
                created += 1

        await db.commit()
        print(f"完成: 新增 {created}, 更新 {updated}, 失败 {failed}（共 {len(matches)} 场）", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
