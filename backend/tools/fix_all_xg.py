"""全局 xG 修复：填充所有 NULL/异常的 xG/xGA"""
import asyncio, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.db.database import engine
from app.db.models import TeamSeasonStats, Team

async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as db:
        r = await db.execute(select(TeamSeasonStats))
        all_stats = list(r.scalars().all())
        print(f"TeamSeasonStats 总数: {len(all_stats)}")

        fixed = 0
        for s in all_stats:
            need = False

            if s.goals_for and s.goals_for > 0:
                need_xg = (s.xG is None or
                           (s.played and s.played > 0 and float(s.xG)/s.played < 0.3) or
                           (s.xG > s.goals_for * 1.1))
                if need_xg:
                    s.xG = float(s.goals_for)
                    need = True

            if s.goals_against and s.goals_against > 0:
                need_xga = (s.xGA is None or
                            (s.played and s.played > 0 and float(s.xGA)/s.played < 0.3) or
                            (s.xGA > s.goals_against * 1.1))
                if need_xga:
                    s.xGA = float(s.goals_against)

            if need:
                fixed += 1
                if fixed <= 10 or fixed % 20 == 0:
                    tr = await db.execute(select(Team).where(Team.id == s.team_id))
                    t = tr.scalar_one_or_none()
                    tn = t.name_zh if t else f"id={s.team_id}"
                    print(f"  {tn:14s} stats_id={s.id:5d} p={s.played} xG:→{s.xG:.0f}")

        await db.flush()
        await db.commit()
        print(f"\n总计修复 {fixed} 条记录")

asyncio.run(main())
