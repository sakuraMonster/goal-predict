"""添加 赫尔辛基(HJK) 和 赫尔辛基火花(HIFK) 到数据库映射"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import Team, TeamAlias
from sqlalchemy import select, and_

MAPPINGS = {
    "赫尔辛基":   {"sm_id": 724,  "name_en": "HJK",  "short": "HJK"},
    "赫尔辛基火花": {"sm_id": 912,  "name_en": "HIFK", "short": "HIFK"},
}

async def main():
    async with async_session() as db:
        for zh, info in MAPPINGS.items():
            sm_id = info["sm_id"]
            # 查找是否已有该 SM ID 的记录
            result = await db.execute(select(Team).where(Team.sportmonks_id == sm_id))
            team = result.scalar_one_or_none()

            if team:
                # 更新中文名
                old_zh = team.name_zh
                team.name_zh = zh
                team.name_en = info["name_en"]
                team.short_en = info["short"]
                team.needs_review = False
                team.review_reason = None
                print(f"  {zh}: 更新现有记录 id={team.id} (旧名: {old_zh})")
            else:
                # 查找待确认的占位记录
                result = await db.execute(select(Team).where(Team.name_zh == zh))
                team = result.scalar_one_or_none()
                if team:
                    team.sportmonks_id = sm_id
                    team.name_en = info["name_en"]
                    team.short_en = info["short"]
                    team.needs_review = False
                    team.review_reason = None
                    print(f"  {zh}: 更新占位记录 id={team.id}")
                else:
                    print(f"  {zh}: 未找到记录，跳过")
                    continue

            # 添加别名
            for alias_name, source in [
                (zh, "sporttery.cn"),
                (info["name_en"], "sportmonks"),
            ]:
                exist = await db.execute(
                    select(TeamAlias).where(and_(
                        TeamAlias.team_id == team.id,
                        TeamAlias.alias_name == alias_name
                    ))
                )
                if not exist.scalar_one_or_none():
                    db.add(TeamAlias(
                        team_id=team.id, alias_name=alias_name,
                        source=source, is_primary=(source == "sporttery.cn")
                    ))
                    print(f"    + 别名: {alias_name} ({source})")

        await db.commit()
        print("\n完成!")

asyncio.run(main())
