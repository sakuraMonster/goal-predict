"""快速检查当前SM fixture匹配状态"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import date
from app.db.database import async_session
from app.db.models import Match
from sqlalchemy import select, func


async def main():
    async with async_session() as db:
        for d in [date(2026,8,1), date(2026,8,2)]:
            result = await db.execute(
                select(Match).where(
                    func.date(Match.kickoff_time) == d
                ).order_by(Match.kickoff_time)
            )
            matches = list(result.scalars().all())
            matched = sum(1 for m in matches if m.sportmonks_fixture_id)
            print(f'{d}: {matched}/{len(matches)} 场有 SM fixture_id')
            for m in matches:
                h = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
                a = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
                fx = m.sportmonks_fixture_id or "NONE"
                print(f'  ID={m.id} SM_fx={fx} | {h} vs {a}')

asyncio.run(main())
