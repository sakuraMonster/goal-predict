"""直接使用 Pipeline 预测昨日6场，绕过 HTTP"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.db.database import async_session
from app.predictor.pipeline import PredictionPipeline

IDS = [15463, 15464, 15465, 15466, 15467, 15468]
NAMES = {
    15463: ("001", "中日德兰", "贝西克塔斯"),
    15464: ("002", "帕福斯", "斯普利特海杜克"),
    15465: ("003", "安德莱赫特", "哈马比"),
    15466: ("004", "费伦茨瓦罗斯", "特温特"),
    15467: ("005", "本菲卡", "圣加仑"),
    15468: ("006", "科林蒂安", "巴拉纳竞技"),
}
SPF_MAP = {1: "主胜", 2: "平局", 3: "客胜"}

async def main():
    async with async_session() as db:
        pipeline = PredictionPipeline(db)

        print(f"\n{'场次':<35} {'主胜':>6} {'平局':>6} {'客胜':>6} │ {'预测':>6} {'实际':>6} {'命中':>4} │ {'冷门':>4} {'进球':>5} {'实际':>6}")
        print("-" * 110)

        hits = 0; total = 0
        for mid in IDS:
            num, ht, at = NAMES[mid]
            name = f"{num} {ht} vs {at}"

            try:
                result = await pipeline.predict(mid)

                hp = result.get("home_prob", 0)
                dp = result.get("draw_prob", 0)
                ap = result.get("away_prob", 0)

                max_p = max(hp, dp, ap)
                if hp == max_p: pred_spf = "主胜"; spf_code = 1
                elif dp == max_p: pred_spf = "平局"; spf_code = 2
                else: pred_spf = "客胜"; spf_code = 3

                # 从 spf_prediction 或推断
                spf_pred = result.get("spf_prediction") or result.get("prediction_spf")

                is_cold = "Y" if result.get("is_cold_match") else "-"
                goals = result.get("expected_goals", 0)

                # 实际结果：从数据库查
                from sqlalchemy import select
                from app.db.models import Match
                mr = await db.execute(select(Match).where(Match.id == mid))
                match = mr.scalar_one_or_none()
                if match:
                    h_score = match.home_score or 0
                    a_score = match.away_score or 0
                    actual_score_str = f"{h_score}:{a_score}"
                    if h_score > a_score: actual_spf = "主胜"; actual_code = 1
                    elif h_score == a_score: actual_spf = "平局"; actual_code = 2
                    else: actual_spf = "客胜"; actual_code = 3
                else:
                    actual_spf = "?"; actual_code = -1; actual_score_str = "?"

                hit = "Y" if spf_code == actual_code else "-"
                if actual_code >= 1:
                    total += 1
                    if hit == "Y": hits += 1

                # 庄家意图
                features_df = await pipeline.feature_engineer.extract_features(mid)
                intent_val = features_df.iloc[0].get("bookmaker_intent", 0) if not features_df.empty else 0

                cold_corrected = "Y" if result.get("cold_correction") else "-"

                print(f"{name:<35} {hp:>6.3f} {dp:>6.3f} {ap:>6.3f} │ {pred_spf:>6} {actual_spf:>6} {hit:>4} │ {is_cold:>4} {goals:>5.1f} {actual_score_str:>6} │ intent={intent_val:+.2f}({cold_corrected})")
            except Exception as e:
                import traceback
                print(f"{name:<35} ERROR: {e.__class__.__name__}: {e}")
                traceback.print_exc()

        print(f"\nSPF命中: {hits}/{total}")

asyncio.run(main())
