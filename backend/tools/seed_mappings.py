"""从 team-alias-seed.json 导入名称映射数据到数据库"""
import asyncio
import json
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import League, LeagueAlias, Team, TeamAlias


async def import_seed():
    with open("../team-alias-seed.json", "r", encoding="utf-8") as f:
        seed = json.load(f)

    async with async_session() as db:
        league_count = 0
        team_count = 0
        alias_count = 0

        for league_data in seed.get("leagues", []):
            league_key = league_data["key"]
            league_label = league_data["label"]
            target_name = league_data["target_league_name"]

            # 查找或创建联赛
            result = await db.execute(
                select(League).where(
                    (League.name_zh == league_label) |
                    (League.name_en == target_name)
                )
            )
            league = result.scalar_one_or_none()

            if not league:
                league = League(
                    name_zh=league_label,
                    name_en=target_name,
                    active=True,
                )
                db.add(league)
                await db.flush()
                league_count += 1

            # 添加联赛别名
            await _ensure_league_alias(db, league.id, target_name, "sportmonks", is_primary=True)

            for alias_data in league_data.get("aliases", []):
                if alias_data.get("status") != "seed_confirmed":
                    continue

                sm_team_id = alias_data["sportmonks_team_id"]
                sm_team_name = alias_data["sportmonks_team_name"]
                zh_name = alias_data["source_team_name_zh"]
                sm_short = alias_data.get("sportmonks_short_code", "")

                # 查找或创建球队
                result = await db.execute(
                    select(Team).where(Team.sportmonks_id == sm_team_id)
                )
                team = result.scalar_one_or_none()

                if not team:
                    team = Team(
                        sportmonks_id=sm_team_id,
                        league_id=league.id,
                        name_zh=zh_name,
                        name_en=sm_team_name,
                        short_en=sm_short,
                    )
                    db.add(team)
                    await db.flush()
                    team_count += 1
                else:
                    # 补充中文名
                    if not team.name_zh:
                        team.name_zh = zh_name
                    if not team.league_id:
                        team.league_id = league.id

                # 添加球队别名
                a1 = await _ensure_alias(db, team.id, zh_name, "sporttery.cn", is_primary=True)
                a2 = await _ensure_alias(db, team.id, sm_team_name, "sportmonks", is_primary=False)
                alias_count += a1 + a2

        await db.commit()
        print(f"导入完成:")
        print(f"  联赛: 新增 {league_count} 个")
        print(f"  球队: 新增/更新 {team_count} 个")
        print(f"  别名: 新增 {alias_count} 条")


async def _ensure_alias(db, entity_id, name, source, is_primary=False) -> int:
    """确保球队别名存在，返回新增数量（0或1）"""
    result = await db.execute(
        select(TeamAlias).where(
            (TeamAlias.team_id == entity_id) &
            (TeamAlias.alias_name == name) &
            (TeamAlias.source == source)
        )
    )
    if result.scalar_one_or_none():
        return 0
    db.add(TeamAlias(
        team_id=entity_id,
        alias_name=name,
        source=source,
        is_primary=is_primary,
    ))
    return 1


async def _ensure_league_alias(db, entity_id, name, source, is_primary=False) -> int:
    """确保联赛别名存在，返回新增数量（0或1）"""
    result = await db.execute(
        select(LeagueAlias).where(
            (LeagueAlias.league_id == entity_id) &
            (LeagueAlias.alias_name == name) &
            (LeagueAlias.source == source)
        )
    )
    if result.scalar_one_or_none():
        return 0
    db.add(LeagueAlias(
        league_id=entity_id,
        alias_name=name,
        source=source,
        is_primary=is_primary,
    ))
    return 1


if __name__ == "__main__":
    asyncio.run(import_seed())
