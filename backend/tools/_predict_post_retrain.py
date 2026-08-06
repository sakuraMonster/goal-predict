"""重训练后预测6场"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.db.database import async_session
from app.predictor.pipeline import PredictionPipeline

IDS = [15463, 15464, 15465, 15466, 15467, 15468]
NAMES = {
    15463: ("001", "中日德兰", "贝西克塔斯", "客胜(0:2)"),
    15464: ("002", "帕福斯", "斯普利特海杜克", "主胜(2:0)"),
    15465: ("003", "安德莱赫特", "哈马比", "主胜(3:1)"),
    15466: ("004", "费伦茨瓦罗斯", "特温特", "平局(2:2)"),
    15467: ("005", "本菲卡", "圣加仑", "主胜(5:0)"),
    15468: ("006", "科林蒂安", "巴拉纳竞技", "平局(0:0)"),
}

async def main():
    async with async_session() as db:
        pipeline = PredictionPipeline(db)

        print(f"\n{'场次':<35} {'主胜':>6} {'平局':>6} {'客胜':>6} │ {'预测':>6} {'实际':>8} {'命中':>4} │ {'冷门':>4} {'进球':>5}")
        print("-" * 100)

        hits = 0
        for mid in IDS:
            num, ht, at, actual_str = NAMES[mid]
            name = f"{num} {ht} vs {at}"
            actual_spf = actual_str.split("(")[0]

            try:
                result = await pipeline.predict(mid)

                hp = result.get("home_prob", 0)
                dp = result.get("draw_prob", 0)
                ap = result.get("away_prob", 0)

                max_p = max(hp, dp, ap)
                if hp == max_p: pred_spf = "主胜"
                elif dp == max_p: pred_spf = "平局"
                else: pred_spf = "客胜"

                hit = "Y" if pred_spf == actual_spf else "-"
                if hit == "Y": hits += 1

                cold = "Y" if result.get("is_cold_match") else "-"
                goals = result.get("expected_goals", 0)

                # 冷门修正详情
                cc = result.get("cold_correction", {})
                div_dir = cc.get("divergence_direction", "") if cc else ""
                intent_app = cc.get("intent_applied", 0) if cc else 0

                print(f"{name:<35} {hp:>6.3f} {dp:>6.3f} {ap:>6.3f} │ {pred_spf:>6} {actual_str:>8} {hit:>4} │ {cold:>4} {goals:>5.1f}")
                if cold == "Y":
                    print(f"  └─ 修正: {div_dir} | intent={intent_app:+.2f}")

            except Exception as e:
                import traceback
                print(f"{name}: ERROR - {e}")
                traceback.print_exc()

        print(f"\nSPF命中: {hits}/6")

asyncio.run(main())
