"""修复队名别名和赛事映射"""
import asyncio
from sqlalchemy import text
from app.db.database import engine

async def run():
    async with engine.begin() as conn:
        # 1. 添加缺失别名
        aliases_to_add = [
            (162, "雅罗", "sporttery.cn"),     # Jaro 的另一个中文名
            (169, "塞伊奈", "sporttery.cn"),   # SJK 的另一个中文名
            (150, "厄格里特", "sporttery.cn"),  # Orgryte 的另一个中文名
        ]
        for team_id, alias, source in aliases_to_add:
            existing = await conn.execute(
                text("SELECT 1 FROM team_aliases WHERE team_id=:tid AND alias_name=:name"),
                {"tid": team_id, "name": alias}
            )
            if existing.first():
                print(f"别名已存在: team_id={team_id} alias='{alias}'")
            else:
                await conn.execute(
                    text("INSERT INTO team_aliases (team_id, alias_name, source) VALUES (:tid, :name, :src)"),
                    {"tid": team_id, "name": alias, "src": source}
                )
                print(f"别名添加: team_id={team_id} alias='{alias}'")

        # 2. 修正 match id=3
        await conn.execute(text("UPDATE matches SET home_team_id=99, away_team_id=98 WHERE id=3"))
        print("match id=3: home_team_id→99, away_team_id→98")

        # 3. 设置 match id=1 的 team_ids
        await conn.execute(text("UPDATE matches SET home_team_id=162, away_team_id=169 WHERE id=1"))
        print("match id=1: home_team_id→162, away_team_id→169")

        # 4. 显示
        result = await conn.execute(
            text("SELECT id, match_num, home_team_name, home_team_id, away_team_name, away_team_id, sportmonks_fixture_id FROM matches ORDER BY id")
        )
        print("\n=== 修复后 ===")
        for row in result:
            print(f"  id={row[0]} num={row[1]} {row[2]}({row[3]}) vs {row[4]}({row[5]}) sm_fx={row[6]}")

asyncio.run(run())
