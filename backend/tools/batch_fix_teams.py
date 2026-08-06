"""批量更新未匹配球队的 SportMonks 映射"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db.database import async_session
from app.db.models import Team, TeamAlias
from sqlalchemy import select

MAPPINGS = {
    "波兹南":   {"sm_id": 302,    "name_en": "Lech Poznań",       "short": "LPO"},
    "奥胡斯":   {"sm_id": 2905,   "name_en": "AGF",               "short": "AGF"},
    "阿拉木图": {"sm_id": 3108,   "name_en": "Kairat",            "short": "KAI"},
    "奥莫尼亚": {"sm_id": 368,    "name_en": "Omonia Nicosia",    "short": "OMO"},
    "哈茨":     {"sm_id": 314,    "name_en": "Hearts",            "short": "HEA"},
    "格风暴":   {"sm_id": 3357,   "name_en": "Sturm Graz",        "short": "STU"},
    "库奥皮奥": {"sm_id": 4323,   "name_en": "KuPS",              "short": "KuPS"},
    "萨巴赫":   {"sm_id": 138649, "name_en": "Sabah FK",          "short": "Sabah"},
    "索尔纳":   {"sm_id": 2825,   "name_en": "AIK",               "short": "AIK"},
    "腓特烈":   {"sm_id": 1743,   "name_en": "Fredrikstad",       "short": "FFK"},
}

async def main():
    async with async_session() as db:
        updated = 0
        skipped = 0
        for zh, info in MAPPINGS.items():
            result = await db.execute(select(Team).where(Team.name_zh == zh))
            team = result.scalar_one_or_none()
            if not team:
                print(f"  {zh}: 数据库中未找到")
                skipped += 1
                continue

            # 检查 sportmonks_id 是否已被其他球队占用
            sm_id = info["sm_id"]
            dup = await db.execute(
                select(Team).where(Team.sportmonks_id == sm_id, Team.id != team.id)
            )
            existing = dup.scalar_one_or_none()
            if existing:
                print(f"  {zh}: sportmonks_id={sm_id} 已被 {existing.name_zh} (id={existing.id}) 占用, 跳过")
                skipped += 1
                continue

            with db.no_autoflush:
                # 添加英文别名
                exist_en = await db.execute(
                    select(TeamAlias).where(
                        TeamAlias.team_id == team.id,
                        TeamAlias.alias_name == info["name_en"]
                    )
                )
                if not exist_en.scalar_one_or_none():
                    db.add(TeamAlias(team_id=team.id, alias_name=info["name_en"], source="sportmonks", is_primary=False))

                # 添加竞彩网中文名别名
                exist_zh = await db.execute(
                    select(TeamAlias).where(
                        TeamAlias.team_id == team.id,
                        TeamAlias.alias_name == zh
                    )
                )
                if not exist_zh.scalar_one_or_none():
                    db.add(TeamAlias(team_id=team.id, alias_name=zh, source="sporttery.cn", is_primary=True))

                # 更新 Team
                team.sportmonks_id = sm_id
                team.name_en = info["name_en"]
                if info["short"]:
                    team.short_en = info["short"]
                team.needs_review = False
                team.review_reason = None

            updated += 1
            print(f"  {zh} → SM id={sm_id}  {info['name_en']}")

        await db.commit()
        print(f"\n完成! 更新 {updated} 支, 跳过 {skipped} 支")

asyncio.run(main())
