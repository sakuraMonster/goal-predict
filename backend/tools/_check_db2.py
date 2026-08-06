import asyncio
from app.db.database import async_session
from sqlalchemy import text

async def check():
    async with async_session() as db:
        # 1. team_aliases for 金泉尚武/大田市民
        r = await db.execute(text("SELECT * FROM team_aliases WHERE alias_name IN ('金泉尚武', '大田市民')"))
        print('=== team_aliases: 金泉尚武/大田市民 ===')
        for row in r.fetchall():
            print(f'  {row}')

        # 2. 所有 league 数据
        r2 = await db.execute(text("SELECT * FROM leagues ORDER BY id"))
        print('\n=== leagues ===')
        for row in r2.fetchall():
            print(f'  {row}')

        # 3. league_aliases
        r3 = await db.execute(text("SELECT * FROM league_aliases ORDER BY id"))
        print('\n=== league_aliases ===')
        for row in r3.fetchall():
            print(f'  {row}')

        # 4. 缺失球队名对应的 team 是否存在
        r4 = await db.execute(text("SELECT id, name_zh, name_en FROM teams WHERE name_zh IN ('金泉尚武', '大田市民', '雅罗', '塞伊奈', '韦斯特罗斯', '厄格里特')"))
        print('\n=== 这6队是否在teams表 ===')
        for row in r4.fetchall():
            print(f'  {row}')

        # 5. 金泉尚武/大田市民 对应的 team 记录及别名
        r5 = await db.execute(text("""
            SELECT ta.*, t.name_zh, t.name_en 
            FROM team_aliases ta 
            JOIN teams t ON t.id = ta.team_id 
            WHERE ta.alias_name IN ('金泉尚武', '大田市民')
        """))
        print('\n=== 金泉尚武/大田市民 别名+team信息 ===')
        for row in r5.fetchall():
            print(f'  {row}')

asyncio.run(check())
