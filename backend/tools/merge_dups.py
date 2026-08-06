"""合并重复球队 + 更新H2H/状态数据"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

from app.db.database import async_session
from app.db.models import Team, TeamAlias, Match, HeadToHead, TeamSeasonStats
from app.collector.pipeline import SyncPipeline
from sqlalchemy import select, update, delete


async def main():
    pipeline = SyncPipeline()

    async with async_session() as db:
        # ====== 1. 合并 尔格里特(id=852) → 奥尔格里特(id=150) ======
        print("1. 合并 尔格里特(852) → 奥尔格里特(150)")
        # 把id=852的所有别名迁移到id=150
        aliases_852 = (await db.execute(select(TeamAlias).where(TeamAlias.team_id == 852))).scalars().all()
        for a in aliases_852:
            # 检查id=150是否已有同名alias
            existing = await db.execute(
                select(TeamAlias).where(TeamAlias.team_id == 150, TeamAlias.alias_name == a.alias_name)
            )
            if not existing.scalar_one_or_none():
                a.team_id = 150
                print(f"  迁移别名: {a.alias_name}")
            else:
                await db.delete(a)
                print(f"  删除重复别名: {a.alias_name}")

        # 清除id=852的错误SM ID，标记为合并
        t852 = (await db.execute(select(Team).where(Team.id == 852))).scalar_one()
        t852.sportmonks_id = None
        t852.name_en = None
        t852.needs_review = True
        t852.review_reason = "已合并到 id=150 奥尔格里特 (Örgryte IS, SM=1870)"
        print(f"  清除了尔格里特(id=852)的错误SM")

        # ====== 2. 添加"赫尔火花"别名到赫尔辛基火花(id=274) ======
        print("\n2. 添加别名")
        existing = await db.execute(
            select(TeamAlias).where(TeamAlias.team_id == 274, TeamAlias.alias_name == "赫尔火花")
        )
        if not existing.scalar_one_or_none():
            db.add(TeamAlias(team_id=274, alias_name="赫尔火花", source="manual"))
            print(f"  添加别名: 赫尔火花 → 赫尔辛基火花(id=274, SM=912)")

        # ====== 3. 合并 奥斯陆KFUM(id=565) → 奥斯KFUM(id=181) ======
        print("\n3. 合并 奥斯陆KFUM(565) → 奥斯KFUM(181)")

        # 把id=565的别名迁移
        aliases_565 = (await db.execute(select(TeamAlias).where(TeamAlias.team_id == 565))).scalars().all()
        for a in aliases_565:
            existing = await db.execute(
                select(TeamAlias).where(TeamAlias.team_id == 181, TeamAlias.alias_name == a.alias_name)
            )
            if not existing.scalar_one_or_none():
                a.team_id = 181
                print(f"  迁移别名: {a.alias_name}")
            else:
                await db.delete(a)

        # 检查是否有match还关联到id=565（当前比赛）
        matches_565 = (await db.execute(
            select(Match).where((Match.home_team_id == 565) | (Match.away_team_id == 565))
        )).scalars().all()
        for m in matches_565:
            if m.home_team_id == 565: m.home_team_id = 181
            if m.away_team_id == 565: m.away_team_id = 181
            print(f"  修正比赛 ID={m.id} team关联 565→181")

        # 清除id=565的错误SM，标记合并
        t565 = (await db.execute(select(Team).where(Team.id == 565))).scalar_one()
        t565.sportmonks_id = None
        t565.name_en = None
        t565.needs_review = True
        t565.review_reason = "已合并到 id=181 奥斯KFUM (KFUM Oslo, SM=11914)"
        print(f"  清除了奥斯陆KFUM(id=565)的错误SM")

        await db.commit()

    # ====== 4. 确认最终状态 ======
    print("\n4. 最终状态")
    async with async_session() as db:
        for tid in [150, 274, 181]:
            t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one()
            aliases = (await db.execute(select(TeamAlias).where(TeamAlias.team_id == tid))).scalars().all()
            print(f"  {t.name_zh}: SM={t.sportmonks_id} name_en={t.name_en}")
            print(f"    别名: {[a.alias_name for a in aliases]}")

    print("\n合并完成! 后续定时任务会自动更新H2H和近期状态。")

asyncio.run(main())
