"""合并重复球队: 波兹南莱赫→波兹南, 里莫→瑞模贝雷"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.db.database import async_session
from sqlalchemy import select, update, and_
from app.db.models import Team, TeamAlias, Match, HeadToHead

async def merge_team(db, src_id: int, dst_id: int, new_name_zh: str, sm_id: int = None):
    """将 src_id 合并到 dst_id，更新中文名"""
    src = await db.execute(select(Team).where(Team.id == src_id))
    dst = await db.execute(select(Team).where(Team.id == dst_id))
    src_team = src.scalar_one_or_none()
    dst_team = dst.scalar_one_or_none()
    if not src_team or not dst_team:
        print(f"  未找到球队: src={src_id} dst={dst_id}")
        return

    old_name = dst_team.name_zh
    print(f"  合并: {src_team.name_zh}(id={src_id}) → {old_name}(id={dst_id})")
    print(f"    新中文名: {new_name_zh}")

    # 1. 更新目标球队
    dst_team.name_zh = new_name_zh
    if sm_id:
        dst_team.sportmonks_id = sm_id
        dst_team.needs_review = False
        dst_team.review_reason = None

    # 2. 添加竞彩网中文名别名
    ex = await db.execute(
        select(TeamAlias).where(and_(TeamAlias.team_id == dst_id, TeamAlias.alias_name == new_name_zh)))
    if not ex.scalar_one_or_none():
        db.add(TeamAlias(team_id=dst_id, alias_name=new_name_zh, source="sporttery.cn", is_primary=True))

    # 3. 迁移比赛关联
    match_result = await db.execute(
        select(Match).where((Match.home_team_id == src_id) | (Match.away_team_id == src_id)))
    migrated = 0
    for m in match_result.scalars().all():
        if m.home_team_id == src_id:
            m.home_team_id = dst_id
        if m.away_team_id == src_id:
            m.away_team_id = dst_id
        migrated += 1
    print(f"    迁移比赛: {migrated} 场")

    # 4. 迁移 H2H 记录
    h2h_result = await db.execute(
        select(HeadToHead).where(
            (HeadToHead.home_team_id == src_id) | (HeadToHead.away_team_id == src_id)))
    h2h_migrated = 0
    for h in h2h_result.scalars().all():
        if h.home_team_id == src_id:
            h.home_team_id = dst_id
        if h.away_team_id == src_id:
            h.away_team_id = dst_id
        h2h_migrated += 1
    print(f"    迁移 H2H: {h2h_migrated} 条")

    # 5. 删除源球队的别名
    pa = await db.execute(select(TeamAlias).where(TeamAlias.team_id == src_id))
    for a in pa.scalars().all():
        await db.delete(a)
    await db.flush()  # 确保别名先删除，避免外键约束冲突

    # 6. 删除源球队
    await db.delete(src_team)

async def main():
    async with async_session() as db:
        # 1. 波兹南莱赫 (292) → 波兹南 (290), SM id=302
        await merge_team(db, 292, 290, "波兹南莱赫", 302)

        # 2. 里莫 (293) → 瑞模贝雷 (253), SM id=11550
        await merge_team(db, 293, 253, "里莫", 11550)

        await db.commit()
        print("\n合并完成！")

    # 验证
    async with async_session() as db:
        r = await db.execute(select(Team).where(Team.id.in_([290, 253])))
        for t in r.scalars().all():
            print(f"  id={t.id} name_zh={t.name_zh} sm_id={t.sportmonks_id} name_en={t.name_en} needs_review={t.needs_review}")

        r = await db.execute(select(Team).where(Team.id.in_([292, 293])))
        deleted = r.scalars().all()
        print(f"  已删除: {[t.name_zh for t in deleted]}")

asyncio.run(main())
