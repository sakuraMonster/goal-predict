"""修复重复球队：为已存在的 SM 映射添加竞彩网别名，删除重复占位记录"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import Team, TeamAlias, Match
from sqlalchemy import select

# 竞彩网占位 → 已存在的正确记录
DUPLICATE_MERGE = {
    # 竞彩网占位名 → 已存在的正确记录 (sportmonks_id)
    "阿拉木图": 3108,   # → 阿拉木图凯拉特
    "库奥皮奥": 4323,   # → 库普斯
    "萨巴赫":   138649, # → 沙巴巴库
    "索尔纳":   2825,   # → AIK索尔纳
    "腓特烈":   1743,   # → 腓特烈斯塔
}

async def main():
    async with async_session() as db:
        for zh, sm_id in DUPLICATE_MERGE.items():
            # 查找占位记录
            placeholder = await db.execute(select(Team).where(Team.name_zh == zh))
            placeholder = placeholder.scalar_one_or_none()
            if not placeholder:
                print(f"  {zh}: 占位记录已不存在")
                continue

            # 查找正确的记录
            correct = await db.execute(
                select(Team).where(Team.sportmonks_id == sm_id, Team.id != placeholder.id)
            )
            correct = correct.scalar_one_or_none()
            if not correct:
                print(f"  {zh}: 未找到 sm_id={sm_id} 的正确记录")
                continue

            # 为正确记录添加竞彩网别名
            exist = await db.execute(
                select(TeamAlias).where(
                    TeamAlias.team_id == correct.id,
                    TeamAlias.alias_name == zh
                )
            )
            if not exist.scalar_one_or_none():
                db.add(TeamAlias(team_id=correct.id, alias_name=zh, source="sporttery.cn", is_primary=False))
                print(f"  {zh}: 为 {correct.name_zh} (id={correct.id}) 添加别名 '{zh}'")
            else:
                print(f"  {zh}: 别名已存在于 {correct.name_zh} (id={correct.id})")

            # 将占位记录的赛事关联迁移到正确记录
            match_result = await db.execute(
                select(Match).where(
                    (Match.home_team_id == placeholder.id) | (Match.away_team_id == placeholder.id)
                )
            )
            matches = match_result.scalars().all()
            for m in matches:
                if m.home_team_id == placeholder.id:
                    m.home_team_id = correct.id
                if m.away_team_id == placeholder.id:
                    m.away_team_id = correct.id
            if matches:
                print(f"    → 迁移 {len(matches)} 场赛事关联")

            # 删除占位别名
            await db.execute(
                select(TeamAlias).where(TeamAlias.team_id == placeholder.id)
            )
            aliases = (await db.execute(
                select(TeamAlias).where(TeamAlias.team_id == placeholder.id)
            )).scalars().all()
            for a in aliases:
                await db.delete(a)

            # 删除占位记录
            await db.delete(placeholder)
            print(f"    → 删除占位记录 id={placeholder.id}")

        await db.commit()
        print("\n完成!")

asyncio.run(main())
