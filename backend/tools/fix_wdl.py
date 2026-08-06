"""清理不完整的 W/D/L 数据，等下轮同步重新填入"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session, engine
from sqlalchemy import text

async def main():
    async with engine.begin() as conn:
        # 找出 W+D+L != played 的记录
        r = await conn.execute(text("""
            SELECT team_id, season, played, wins, draws, losses
            FROM team_season_stats
            WHERE played > 0 AND (wins + draws + losses) != played
        """))
        bad = r.fetchall()
        print(f"不完整记录: {len(bad)} 条")
        for row in bad:
            print(f"  team_id={row[0]} season={row[1]} P={row[2]} W={row[3]} D={row[4]} L={row[5]} (sum={row[3]+row[4]+row[5]})")
        
        # 将 W/D/L 归零，让下次 sync 重新从 SM 获取
        result = await conn.execute(text("""
            UPDATE team_season_stats 
            SET wins=0, draws=0, losses=0 
            WHERE played > 0 AND (wins + draws + losses) != played
        """))
        print(f"\n已重置 {result.rowcount} 条记录的 W/D/L")

asyncio.run(main())
