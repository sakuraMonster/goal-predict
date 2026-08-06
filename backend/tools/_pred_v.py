"""完整预测验证"""
import asyncio, sys, os, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

OUT = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_pred_out.txt"), "w", encoding="utf-8")

def log(msg):
    OUT.write(msg + "\n")
    OUT.flush()
    print(msg, flush=True)

async def main():
    try:
        from app.db.database import async_session
        from app.predictor.pipeline import PredictionPipeline
        
        log("Starting prediction...")
        async with async_session() as db:
            pipeline = PredictionPipeline(db)
            mid = 15465
            result = await pipeline.predict(mid)
            
            log(f"=== 周四003 安德莱赫特 vs 哈马比 ===")
            log(f"SPF: 主{result.get('home_prob',0):.4f} 平{result.get('draw_prob',0):.4f} 客{result.get('away_prob',0):.4f}")
            log(f"让球: 主{result.get('handicap_home_prob',0):.4f} 平{result.get('handicap_draw_prob',0):.4f} 客{result.get('handicap_away_prob',0):.4f}")
            log(f"冷门: {result.get('is_cold_match')}")
            log(f"预期进球: {result.get('expected_goals', 0):.2f}")
            log(f"置信度: {result.get('confidence_level')}")
            
            cc = result.get("cold_correction", {})
            if cc:
                log(f"冷门修正详情: {cc}")
            
            log(f"比分Top5: {result.get('score_top5_json')}")
            log(f"摘要: {result.get('summary_text')}")
            log("DONE")
    except Exception as e:
        log(f"ERROR: {e}")
        traceback.print_exc(file=OUT)
        OUT.flush()

asyncio.run(main())
