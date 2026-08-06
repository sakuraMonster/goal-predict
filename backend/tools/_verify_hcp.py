import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from app.db.database import async_session
from app.predictor.pipeline import PredictionPipeline

async def main():
    async with async_session() as db:
        pipeline = PredictionPipeline(db)
        r = await pipeline.predict(15471)  # 周五003
        spf_h, spf_d, spf_a = r['home_prob'], r['draw_prob'], r['away_prob']
        hcp_h, hcp_d, hcp_a = r['handicap_home_prob'], r['handicap_draw_prob'], r['handicap_away_prob']
        
        print(f"胜平负: {spf_h:.1%} / {spf_d:.1%} / {spf_a:.1%}")
        print(f"让球(-1): {hcp_h:.1%} / {hcp_d:.1%} / {hcp_a:.1%}")
        
        # 验证：让负 = 平+客 ≥ 平局概率
        print(f"\n逻辑验证：")
        print(f"  让负 ({hcp_a:.1%}) >= 平局 ({spf_d:.1%})? {'YES' if hcp_a >= spf_d * 0.99 else 'NO - BUG!'}")
        print(f"  让胜+让平 ({hcp_h+hcp_d:.1%}) ~= 主胜 ({spf_h:.1%})? {'YES' if abs(hcp_h+hcp_d - spf_h) < 0.05 else 'big gap:' + str(abs(hcp_h+hcp_d - spf_h))}")
        print(f"  预期进球: {r['expected_goals']:.1f}")
        
        # 检查 prediction 表存储值
        from sqlalchemy import select
        from app.db.models import Prediction
        r2 = await db.execute(select(Prediction).where(Prediction.match_id == 15471))
        pred = r2.scalar_one_or_none()
        if pred:
            print(f"\nDB存储: HCP={pred.handicap_home_prob:.1%}/{pred.handicap_draw_prob:.1%}/{pred.handicap_away_prob:.1%}")

asyncio.run(main())
