"""对比周五001/003的SPF vs HCP混合效果"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from app.db.database import async_session
from app.db.models import Prediction
from sqlalchemy import select

async def main():
    async with async_session() as db:
        for mid, label in [(15469, "周五001"), (15471, "周五003")]:
            r = await db.execute(select(Prediction).where(Prediction.match_id == mid))
            p = r.scalar_one()
            print(f"{label}: SPF={p.home_prob:.1%}/{p.draw_prob:.1%}/{p.away_prob:.1%}  "
                  f"HCP={p.handicap_home_prob:.1%}/{p.handicap_draw_prob:.1%}/{p.handicap_away_prob:.1%}  "
                  f"goals={p.expected_goals:.1f}")

asyncio.run(main())
