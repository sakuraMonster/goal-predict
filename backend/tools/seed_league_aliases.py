"""添加 竞彩网 常用联赛别名到 league_aliases 表"""
import asyncio
from app.db.database import async_session
from app.db.models import League, LeagueAlias
from sqlalchemy import select

LEAGUE_ALIAS_MAP = {
    "英超": "英超",
    "西甲": "西甲",
    "德甲": "德甲",
    "意甲": "意甲",
    "法甲": "法甲",
    "日职": "日职",
    "韩职": "韩K",
    "澳超": "澳超",
    "瑞典超": "瑞典超",
    "挪超": "挪超",
    "美职联": "美职联",
    "欧冠": "欧冠",
}

# 竞彩网 可能使用的联赛名 → 系统中已存在的联赛名
JCZQ_LEAGUE_MAP = {
    "英格兰超级联赛": "英超",
    "西班牙甲级联赛": "西甲",
    "德国甲级联赛": "德甲",
    "意大利甲级联赛": "意甲",
    "法国甲级联赛": "法甲",
    "日本职业联赛": "日职",
    "韩国职业联赛": "韩职",
    "澳大利亚超级联赛": "澳超",
    "瑞典超级联赛": "瑞典超",
    "挪威超级联赛": "挪超",
    "美国职业大联盟": "美职联",
    "欧洲冠军联赛": "欧冠",
    "芬兰超级联赛": "芬超",
    "瑞超": "瑞典超",
    "俄超": "俄超",
    "荷甲": "荷甲",
    "葡超": "葡超",
    "比甲": "比甲",
    "苏超": "苏超",
    "土超": "土超",
    "英冠": "英冠",
    "德乙": "德乙",
    "法乙": "法乙",
    "西乙": "西乙",
    "意乙": "意乙",
    "巴甲": "巴甲",
    "阿甲": "阿甲",
    "日乙": "日乙",
    "韩K2": "韩K2",
    "中超": "中超",
    "欧联": "欧联",
    "欧会杯": "欧协联",
    "世预赛": "世预赛",
    "欧预赛": "欧预赛",
}


async def seed_league_aliases():
    async with async_session() as db:
        added = 0
        for jczq_name, target_name in JCZQ_LEAGUE_MAP.items():
            # 查找目标联赛
            result = await db.execute(
                select(League).where(League.name_zh == target_name)
            )
            league = result.scalar_one_or_none()
            if not league:
                print(f"  未找到联赛: {target_name}，跳过 {jczq_name}")
                continue

            # 检查别名是否已存在
            result = await db.execute(
                select(LeagueAlias).where(
                    LeagueAlias.league_id == league.id,
                    LeagueAlias.alias_name == jczq_name,
                )
            )
            if result.scalar_one_or_none():
                continue

            db.add(LeagueAlias(
                league_id=league.id,
                alias_name=jczq_name,
                source="sporttery.cn",
                is_primary=False,
            ))
            added += 1
            print(f"  添加: {jczq_name} → {target_name} (league_id={league.id})")

        await db.commit()
        print(f"共添加 {added} 条联赛别名")


if __name__ == "__main__":
    asyncio.run(seed_league_aliases())
