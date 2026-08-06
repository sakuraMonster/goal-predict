import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Team, TeamAlias, Match

async def run():
    async with async_session() as db:
        # Check team aliases
        result = await db.execute(select(TeamAlias))
        aliases = result.scalars().all()
        print("=== 现有 Team Aliases ===")
        for a in aliases:
            print(f"  team_id={a.team_id} alias='{a.alias_name}' source={a.source}")

        # Check team names we need to match
        print("\n=== DB Teams (前15) ===")
        result = await db.execute(select(Team).limit(15))
        for t in result.scalars().all():
            print(f"  id={t.id} zh='{t.name_zh}' en='{t.name_en}'")

        # 竞彩网 matches
        print("\n=== 竞彩网 Matches ===")
        result = await db.execute(select(Match))
        for m in result.scalars().all():
            print(f"  id={m.id} {m.home_team_name} vs {m.away_team_name} (team_ids: {m.home_team_id}, {m.away_team_id})")

asyncio.run(run())
