"""最终修复：SM ID + 直接匹配fixture + 赔率 + 预测"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, date
from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import Match, Team, Prediction
from app.predictor.pipeline import PredictionPipeline
from sqlalchemy import select, func


async def main():
    sm = SportMonksClient()

    # Step 1: 搜索并修复奥斯陆KFUM
    print("Step 1: 搜索奥斯陆KFUM的正确SM fixtures")
    # 查询 fixtures/date/2026-08-02 中包含 11914 (KFUM) 的fixture
    fixtures = await sm.get_fixtures_by_date("2026-08-02", includes="participants")
    for fx in fixtures:
        pids = set()
        for p in fx.get("participants", []):
            if isinstance(p, dict):
                pids.add(p.get("id"))
        if 11914 in pids:  # KFUM Oslo
            other_pids = pids - {11914}
            print(f"  fixture_id={fx['id']} name={fx.get('name')} pids={pids}")
    
    # 也查 08-01
    fixtures2 = await sm.get_fixtures_by_date("2026-08-01", includes="participants")
    for fx in fixtures2:
        pids = set()
        for p in fx.get("participants", []):
            if isinstance(p, dict):
                pids.add(p.get("id"))
        if 11914 in pids:
            print(f"  fixture_id={fx['id']} name={fx.get('name')} pids={pids}")

    # 查询 fixtures/between 包含 2617+5696 或 1870+2825 或 11914+869
    all_fx = []
    for d_str in ["2026-08-01", "2026-08-02"]:
        fx_list = await sm.get_fixtures_by_date(d_str, includes="participants")
        all_fx.extend(fx_list)

    # 查 15494: AC奥卢(5696) vs Ilves(2617)
    print("\n查 15494: 5696 vs 2617")
    for fx in all_fx:
        pids = {p.get("id") for p in fx.get("participants", []) if isinstance(p, dict)}
        if 5696 in pids and 2617 in pids:
            print(f"  ✓ fixture_id={fx['id']} name={fx.get('name')}")

    # 查 15495: AIK(2825) vs Orgryte(1870)
    print("\n查 15495: 2825 vs 1870")
    for fx in all_fx:
        pids = {p.get("id") for p in fx.get("participants", []) if isinstance(p, dict)}
        if 2825 in pids and 1870 in pids:
            print(f"  ✓ fixture_id={fx['id']} name={fx.get('name')}")

    # 查 15496: KFUM(11914) vs Kristiansund(869)
    print("\n查 15496: 11914 vs 869")
    for fx in all_fx:
        pids = {p.get("id") for p in fx.get("participants", []) if isinstance(p, dict)}
        if 11914 in pids and 869 in pids:
            print(f"  ✓ fixture_id={fx['id']} name={fx.get('name')}")
        elif 11914 in pids:
            other = pids - {11914}
            print(f"  ~ KFUM found in fixture_id={fx['id']} name={fx.get('name')} other_ids={other}")

    await sm.close()

    # Step 2: 修复奥斯陆KFUM SM ID
    print("\nStep 2: 修复SM ID")
    async with async_session() as db:
        r = await db.execute(select(Team).where(Team.id == 565))
        t = r.scalar_one_or_none()
        if t:
            # Check if 11914 already assigned
            existing = await db.execute(select(Team).where(Team.sportmonks_id == 11914, Team.id != 565))
            dup = existing.scalar_one_or_none()
            if dup:
                print(f"  SM=11914 已被 {dup.name_zh}(id={dup.id}) 占用，先清除")
                dup.sportmonks_id = None
                dup.name_en = None
                dup.needs_review = True
                await db.flush()
            print(f"  {t.name_zh}: SM {t.sportmonks_id}→11914, name_en '{t.name_en}'→'KFUM Oslo'")
            t.sportmonks_id = 11914
            t.name_en = "KFUM Oslo"
            t.needs_review = False
            t.review_reason = None
        await db.commit()

    # Step 3: 设置 fixture IDs
    print("\nStep 3: 设置 fixture IDs")
    fixture_map = {}
    
    # 从上面搜索结果手动设置
    # 15494: AC奥卢 vs Ilves → fixture 19635703
    # 15495: AIK vs Orgryte → fixture 19635935
    # 15496: KFUM vs Kristiansund → need to find
    
    async with async_session() as db:
        for mid, fx_id in fixture_map.items():
            r = await db.execute(select(Match).where(Match.id == mid))
            m = r.scalar_one_or_none()
            if m and not m.sportmonks_fixture_id:
                m.sportmonks_fixture_id = fx_id
                print(f"  ID={mid}: fixture_id={fx_id}")
        await db.commit()

    print("done")

asyncio.run(main())
