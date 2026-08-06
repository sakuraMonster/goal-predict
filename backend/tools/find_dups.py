"""查找重复球队并修复"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

from app.db.database import async_session
from app.db.models import Team, TeamAlias, Match
from sqlalchemy import select


async def main():
    async with async_session() as db:
        # 1. 查找所有可能重复的球队（同一SM ID被多个team_id使用？不会，有unique约束）
        # 实际问题是：同一个真实球队被创建了多条Team记录，各有不同SM ID
        
        # 用户指出的重复对：
        pairs = [
            (150, 852, "奥尔格里特/厄尔格里特"),
            (274, None, "赫尔辛基火花/赫尔火花"),
            (181, 565, "奥斯KFUM/奥斯陆KFUM"),
        ]
        
        for tid1, tid2, label in pairs:
            print(f"\n{'='*60}")
            print(f"{label}:")
            for tid in [tid1, tid2]:
                if tid is None:
                    # 搜索"赫尔火花"
                    r = await db.execute(select(Team).where(Team.name_zh.contains("赫尔")))
                    for t in r.scalars().all():
                        print(f"  id={t.id} name_zh={t.name_zh} name_en={t.name_en} SM={t.sportmonks_id}")
                    continue
                    
                r = await db.execute(select(Team).where(Team.id == tid))
                t = r.scalar_one_or_none()
                if t:
                    # 查这个球队关联的比赛
                    mr = await db.execute(
                        select(Match).where(
                            (Match.home_team_id == tid) | (Match.away_team_id == tid)
                        ).order_by(Match.kickoff_time.desc()).limit(5)
                    )
                    matches = mr.scalars().all()
                    print(f"  id={t.id} name_zh={t.name_zh} name_en={t.name_en} SM={t.sportmonks_id}")
                    print(f"    关联比赛: {len(matches)} 场")
                    for m in matches:
                        print(f"      ID={m.id} {m.home_team_name} vs {m.away_team_name} {m.kickoff_time}")
                    
                    # 查别名
                    ar = await db.execute(select(TeamAlias).where(TeamAlias.team_id == tid))
                    aliases = [a.alias_name for a in ar.scalars().all()]
                    if aliases:
                        print(f"    别名: {aliases}")
        
        # 2. 查找更多可能的重复：同一SM ID
        print(f"\n{'='*60}")
        print("查找同一SM ID的重复:")
        # SM ID有unique约束，所以不应该有。检查name_zh重复
        from sqlalchemy import func
        dup_r = await db.execute(
            select(Team.name_zh, func.count(Team.id).label('cnt'), func.array_agg(Team.id))
            .group_by(Team.name_zh).having(func.count(Team.id) > 1)
        )
        for row in dup_r:
            print(f"  {row[0]}: {row[1]}条 ids={row[2]}")


asyncio.run(main())
