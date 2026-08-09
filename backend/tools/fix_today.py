"""修复联赛和 SM fixture 缺失"""
import asyncio
from sqlalchemy import select, update
from app.db.database import async_session, engine
from app.db.models import League, Match

async def main():
    async with async_session() as db:
        # ── 1. 更新已有欧冠的 SM ID ──
        ucl = await db.get(League, 8)
        if ucl and not ucl.sportmonks_id:
            ucl.sportmonks_id = 2
            print(f"更新 欧冠(id=8): sm_id=2")

        # ── 2. 新增联赛 ──
        new_leagues = [
            {"name_zh": "欧罗巴", "name_en": "Europa League", "sm_id": 5, "country": "Europe"},
            {"name_zh": "欧协联", "name_en": "Europa Conference League", "sm_id": 2286, "country": "Europe"},
            {"name_zh": "巴西杯", "name_en": "Copa do Brasil", "sm_id": 654, "country": "Brazil"},
        ]
        league_ids = {}
        for nl in new_leagues:
            # 检查是否已存在
            r = await db.execute(select(League).where(League.name_zh == nl["name_zh"]))
            existing = r.scalar_one_or_none()
            if existing:
                league_ids[nl["sm_id"]] = existing.id
                print(f"联赛已存在: {nl['name_zh']} (id={existing.id})")
            else:
                league = League(
                    name_zh=nl["name_zh"],
                    name_en=nl["name_en"],
                    sportmonks_id=nl["sm_id"],
                    country=nl["country"],
                    active=True,
                )
                db.add(league)
                await db.flush()
                league_ids[nl["sm_id"]] = league.id
                print(f"新增联赛: {nl['name_zh']} (id={league.id}, sm_id={nl['sm_id']})")
        
        await db.commit()
        
        # ── 3. 修正赛事 ──
        fixes = [
            # (match_id, league_key, sm_fixture_id, is_swapped)
            (15513, 654, 19710148, False),   # 巴西杯: 弗鲁米嫩塞 vs 瓦斯科达伽马
            (15514, 654, 19710145, False),   # 巴西杯: 维多利亚 vs 巴拉纳竞技
            # 欧罗巴 - 注意 SM 中主客顺序
            (15515, 5, 19766406, True),      # SM: Universitatea Craiova vs KuPS → DB: 库奥皮奥 vs 克拉约瓦大学 (互换)
            (15516, 5, 19766402, False),     # SM: Jagiellonia vs Rangers → DB: 比亚韦斯托克 vs 流浪者
            (15517, 5, 19766398, False),     # SM: PAOK vs Anderlecht → DB: 塞萨洛尼基 vs 安德莱赫特
            (15518, 5, 19766404, True),      # SM: Hearts vs Benfica → DB: 本菲卡 vs 哈茨 (互换)
        ]
        
        for mid, league_sm_id, sm_fx_id, swapped in fixes:
            match = await db.get(Match, mid)
            if not match:
                print(f"赛事 {mid} 不存在!")
                continue
            lid = league_ids.get(league_sm_id)
            match.league_id = lid
            match.sportmonks_fixture_id = sm_fx_id
            match.is_swapped = swapped
            print(f"赛事 {mid} (jc={match.jc_match_id}): league_id={lid}, sm_fixture={sm_fx_id}, swapped={swapped}")
        
        await db.commit()
        print("\n全部修复完成!")

asyncio.run(main())
