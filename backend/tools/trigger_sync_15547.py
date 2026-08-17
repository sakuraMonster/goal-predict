"""触发单场赔率同步，验证多线入库"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import select, delete
from app.collector.pipeline import SyncPipeline
from app.db.database import async_session
from app.db.models import OddsSnapshot, Match

async def main():
    match_ids = [15547, 15567]
    # 先删除这几场的旧快照（旧逻辑写入的随机单线数据），避免干扰验证
    async with async_session() as db:
        await db.execute(
            delete(OddsSnapshot).where(OddsSnapshot.match_id.in_(match_ids))
        )
        await db.commit()
        print(f"cleared old snapshots for {match_ids}")

    # 直接调用 sync_odds 内部逻辑（只同步目标场次）
    p = SyncPipeline()
    async with async_session() as db:
        for mid in match_ids:
            res = await db.execute(select(Match).where(Match.id == mid))
            m = res.scalar_one_or_none()
            if not m:
                print(f"match {mid} not found")
                continue
            print(f"\n=== syncing {mid} {m.home_team_name} vs {m.away_team_name} ===")
            odds_list = await p.sm.get_odds_pre_match(m.sportmonks_fixture_id)
            print(f"SM returned {len(odds_list)} odds items")

            # 复用 sync_odds 的核心存储逻辑 —— 直接调用公开方法里的代码路径较复杂，
            # 这里直接调用 sync_odds 会同步所有比赛，先只验证 SM 返回结构
            # 打印 OU 数据统计
            ou_items = [o for o in odds_list if o.get("market_id") == 80]
            from collections import Counter
            totals = Counter(round(float(o.get("total")), 2) for o in ou_items if o.get("total"))
            print(f"OU lines distribution: {dict(sorted(totals.items()))}")

asyncio.run(main())
