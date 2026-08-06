import asyncio
from sqlalchemy import select, text
from app.db.database import async_session, engine

async def run():
    # Direct SQL to avoid any caching
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT id, jc_match_id, match_num, home_team_name, home_team_id, away_team_name, away_team_id, sportmonks_fixture_id FROM matches"))
        for row in result:
            print(f"id={row[0]} jc={row[1]} num={row[2]} home='{row[3]}' ht_id={row[4]} away='{row[5]}' at_id={row[6]} sm_fx={row[7]}")

asyncio.run(run())
