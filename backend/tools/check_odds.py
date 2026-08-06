import asyncio
from sqlalchemy import text, select, func
from app.db.database import async_session, engine
from app.db.models import Match, OddsSnapshot

async def run():
    async with async_session() as db:
        # Clear old snapshots
        await db.execute(text('DELETE FROM odds_snapshots'))
        await db.commit()
        print("旧赔率已清空")

        # Check match_num
        result = await db.execute(select(Match.id, Match.jc_match_id, Match.match_num, Match.home_team_name))
        print("\n当前赛事:")
        for row in result.all():
            print(f"  id={row[0]} jc_id={row[1]} num={row[2]} {row[3]}")

asyncio.run(run())
