"""重置错误的 form/recent_matches 数据"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import engine
from sqlalchemy import text

async def main():
    async with engine.begin() as conn:
        # 找出所有 form 全是同一字符的记录（大概率是错误数据）
        r = await conn.execute(text("""
            SELECT team_id, season, form FROM team_season_stats
            WHERE form IS NOT NULL AND form != ''
              AND form = REPEAT(LEFT(form, 1), LENGTH(form))
        """))
        bad = r.fetchall()
        print(f"可疑 form: {len(bad)} 条（全是同一字符）")
        for row in bad[:10]:
            print(f"  team_id={row[0]} season={row[1]} form={row[2]}")

        # 清空这些 form 和 recent_matches，下次 sync 重新获取
        r = await conn.execute(text("""
            UPDATE team_season_stats
            SET form = NULL, recent_matches = NULL
            WHERE form IS NOT NULL AND form != ''
              AND form = REPEAT(LEFT(form, 1), LENGTH(form))
        """))
        print(f"\n已清理 {r.rowcount} 条记录的 form/recent_matches")

asyncio.run(main())
