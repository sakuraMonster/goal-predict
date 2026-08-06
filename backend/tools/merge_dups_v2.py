"""合并重复球队 v2 - 精准处理"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from app.db.database import async_session
from app.db.models import Team, TeamAlias, Match
from sqlalchemy import select


async def main():
    async with async_session() as db:
        # ====== 1. 合并 尔格里特(852)别名 → 奥尔格里特(150) ======
        print("1. 合并别名")
        for src_tid, dst_tid in [(852, 150), (565, 181)]:
            src = (await db.execute(select(Team).where(Team.id == src_tid))).scalar_one()
            dst = (await db.execute(select(Team).where(Team.id == dst_tid))).scalar_one()
            aliases = (await db.execute(select(TeamAlias).where(TeamAlias.team_id == src_tid))).scalars().all()
            for a in aliases:
                exist = await db.execute(select(TeamAlias).where(TeamAlias.team_id == dst_tid, TeamAlias.alias_name == a.alias_name))
                if not exist.scalar_one_or_none():
                    a.team_id = dst_tid
                    print(f"  迁移别名: {a.alias_name} {src_tid}→{dst_tid}")
                else:
                    await db.delete(a)
            # 清除源球队错误SM
            if src.sportmonks_id and src.sportmonks_id != dst.sportmonks_id:
                print(f"  清除 {src.name_zh}: SM={src.sportmonks_id}")
                src.sportmonks_id = None
                src.name_en = None
                src.needs_review = True
                src.review_reason = f"已合并到 id={dst_tid} {dst.name_zh}"

        # ====== 2. 添加别名 ======
        print("\n2. 添加别名")
        new_aliases = [
            (274, "赫尔火花"),
        ]
        for tid, name in new_aliases:
            exist = await db.execute(select(TeamAlias).where(TeamAlias.team_id == tid, TeamAlias.alias_name == name))
            if not exist.scalar_one_or_none():
                db.add(TeamAlias(team_id=tid, alias_name=name, source="manual"))
                print(f"  + {name} → team_id={tid}")

        await db.commit()

    # ====== 3. 最终状态 ======
    print("\n3. 最终状态")
    async with async_session() as db:
        for tid in [150, 274, 181]:
            t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one()
            aliases = (await db.execute(select(TeamAlias).where(TeamAlias.team_id == tid))).scalars().all()
            print(f"  {t.name_zh}: SM={t.sportmonks_id} name_en={t.name_en}")
            print(f"    别名: {sorted(a.alias_name for a in aliases)}")

    print("\n完成!")

asyncio.run(main())
