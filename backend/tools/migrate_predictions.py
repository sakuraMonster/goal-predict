"""迁移 + 回填 predictions 表"""
import asyncio, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()

from app.db.database import async_session, engine
from app.db.models import Prediction, Match
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

async def migrate(db: AsyncSession):
    """添加新字段"""
    columns = [
        ("league_id", "INTEGER REFERENCES leagues(id)"),
        ("actual_home_score", "INTEGER"),
        ("actual_away_score", "INTEGER"),
        ("actual_total_goals", "INTEGER"),
        ("result_spf", "INTEGER DEFAULT 0"),
        ("result_hcp", "INTEGER DEFAULT 0"),
        ("result_goals", "INTEGER DEFAULT 0"),
        ("result_score", "INTEGER DEFAULT 0"),
    ]
    for col, col_type in columns:
        try:
            await db.execute(text(f"ALTER TABLE predictions ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception as e:
            # 字段可能已存在
            print(f"  {col}: {e}")
    await db.commit()
    print("迁移完成")


async def backfill(db: AsyncSession):
    """回填充已有预测记录的实际结果 + league_id"""
    print("\n回填中...")
    
    # 1. 回填 league_id (从 match 表关联)
    result = await db.execute(text("""
        UPDATE predictions p SET league_id = m.league_id
        FROM matches m WHERE p.match_id = m.id AND p.league_id IS NULL
    """))
    await db.commit()
    
    # 2. 回填实际比分和四种玩法结果
    predictions = await db.execute(
        select(Prediction, Match).join(Match, Prediction.match_id == Match.id)
        .where(Match.home_score.isnot(None))
    )
    
    updated = 0
    for pred, match in predictions.all():
        if match.home_score is None or match.away_score is None:
            continue
        
        hs, asc = match.home_score, match.away_score
        
        # 实际比分
        pred.actual_home_score = hs
        pred.actual_away_score = asc
        pred.actual_total_goals = hs + asc
        
        # 胜平负结果
        actual_wl = 0 if hs > asc else 1 if hs == asc else 2
        pred_wl = 0 if (pred.home_prob or 0) >= max(pred.draw_prob or 0, pred.away_prob or 0) else \
                  1 if (pred.draw_prob or 0) >= max(pred.home_prob or 0, pred.away_prob or 0) else 2
        pred.result_spf = 1 if actual_wl == pred_wl else -1
        
        # 让球结果
        adj = hs + (match.handicap_line or 0)
        actual_hcp = 0 if adj > asc else 1 if adj == asc else 2
        pred_hcp = 0 if (pred.handicap_home_prob or 0) >= max(pred.handicap_draw_prob or 0, pred.handicap_away_prob or 0) else \
                   1 if (pred.handicap_draw_prob or 0) >= max(pred.handicap_home_prob or 0, pred.handicap_away_prob or 0) else 2
        pred.result_hcp = 1 if actual_hcp == pred_hcp else -1
        
        # 进球数结果（±1 容差）
        total = hs + asc
        diff = abs(total - (pred.expected_goals or 2.5))
        pred.result_goals = 1 if diff <= 1 else -1
        
        # 比分结果（Top5 中是否包含）
        top5 = pred.score_top5_json or []
        actual_score = f"{hs}:{asc}"
        pred.result_score = 1 if any(s.get("score") == actual_score for s in top5) else -1
        
        updated += 1
        if updated % 500 == 0:
            await db.commit()
            print(f"  回填: {updated}")
    
    await db.commit()
    print(f"回填完成: {updated} 条")


async def main():
    async with async_session() as db:
        print("=== 数据库迁移 ===")
        await migrate(db)
        
        print("\n=== 回填已有记录 ===")
        await backfill(db)

if __name__ == "__main__":
    asyncio.run(main())
