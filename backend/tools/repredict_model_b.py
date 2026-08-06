"""Model B 独立重预测工具

默认模式（安全）：仅回填 snap_top2，不覆盖任何已有预测值。
  python tools/repredict_model_b.py 2026-08-02

完整重跑模式（谨慎）：重新跑 pipeline，会用新特征覆盖 expected_goals 等字段。
  python tools/repredict_model_b.py 2026-08-02 --full
"""
import asyncio, sys, os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.snap import snap_top2


async def backfill_snap_only(start_date: str, end_date: str | None = None):
    """仅回填 snap_top2 + result_goals，不碰任何预测值"""
    d_start = datetime.strptime(start_date, "%Y-%m-%d")
    d_end = datetime.strptime(end_date or start_date, "%Y-%m-%d") + timedelta(days=1)
    query_start = d_start.replace(hour=12)
    query_end = d_end.replace(hour=12)

    async with async_session() as db:
        result = await db.execute(
            select(Prediction).where(
                Prediction.kickoff_time >= query_start,
                Prediction.kickoff_time < query_end,
            )
        )
        preds = list(result.scalars().all())
        if not preds:
            print(f"未找到 [{start_date} ~ {end_date or start_date}] 的预测")
            return

        updated = 0
        for p in preds:
            if not p.expected_goals:
                continue
            p.snap_top2 = snap_top2(p.expected_goals)
            if p.actual_total_goals is not None:
                p.result_goals = 1 if p.actual_total_goals in p.snap_top2 else -1
            updated += 1
            print(f"  id={p.id}: eg={p.expected_goals:.2f}, snap={p.snap_top2}, goals_hit={p.result_goals}")

        await db.commit()
        print(f"\n回填完成: {updated} 条")


async def repredict_full(start_date: str, end_date: str | None = None):
    """完整重跑 pipeline（谨慎：会覆盖 expected_goals 等）"""
    from app.predictor.pipeline import PredictionPipeline

    d_start = datetime.strptime(start_date, "%Y-%m-%d")
    d_end = datetime.strptime(end_date or start_date, "%Y-%m-%d") + timedelta(days=1)
    query_start = d_start.replace(hour=12)
    query_end = d_end.replace(hour=12)

    async with async_session() as db:
        result = await db.execute(
            select(Match).where(
                Match.kickoff_time >= query_start,
                Match.kickoff_time < query_end,
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        if not matches:
            print(f"未找到 [{start_date} ~ {end_date or start_date}] 的比赛")
            return

        print(f"找到 {len(matches)} 场比赛 [{query_start} ~ {query_end})")
        print("⚠ 完整重跑模式：将覆盖 expected_goals 等预测值，仅保留 Model A 字段不变")

        pipeline = PredictionPipeline(db)
        model_version = datetime.now().strftime("%Y%m%d-%H%M")
        updated, failed = 0, 0

        for m in matches:
            try:
                pred_result = await pipeline.predict(m.id)
            except Exception as e:
                failed += 1
                print(f"  [FAIL] ID={m.id}: {e}")
                continue

            existing = await db.execute(
                select(Prediction).where(Prediction.match_id == m.id)
            )
            pred = existing.scalar_one_or_none()
            if not pred:
                continue

            pred.expected_goals = pred_result["expected_goals"]
            pred.over_2_5_prob = pred_result["over_2_5_prob"]
            pred.goal_distribution = pred_result["goal_distribution"]
            pred.snap_top2 = pred_result["snap_top2"]
            pred.score_top5_json = pred_result["score_top5_json"]
            pred.summary_text = pred_result.get("summary_text", "")
            pred.key_factors = pred_result.get("key_factors", "")
            pred.model_version = model_version

            if pred.actual_total_goals is not None and pred.snap_top2:
                pred.result_goals = 1 if pred.actual_total_goals in pred.snap_top2 else -1

            updated += 1
            print(f"  [OK] ID={m.id}: λ={pred_result['expected_goals']:.2f}")

        await db.commit()
        print(f"\n完成: {model_version} | 更新 {updated} | 失败 {failed}")


if __name__ == "__main__":
    full_mode = "--full" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if len(args) == 0:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if full_mode:
            asyncio.run(repredict_full(yesterday))
        else:
            asyncio.run(backfill_snap_only(yesterday))
    elif len(args) == 1:
        if full_mode:
            asyncio.run(repredict_full(args[0]))
        else:
            asyncio.run(backfill_snap_only(args[0]))
    else:
        if full_mode:
            asyncio.run(repredict_full(args[0], args[1]))
        else:
            asyncio.run(backfill_snap_only(args[0], args[1]))
