"""检查 SM pre-match 端点 OU (market=80) 原始响应结构：label/total 字段"""
import sys, os, asyncio, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载 .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import Match
from sqlalchemy import select

async def main():
    async with async_session() as db:
        # 找一场今天的比赛
        res = await db.execute(
            select(Match).where(Match.id == 15547)
        )
        m = res.scalar_one_or_none()
        if not m:
            print("match not found")
            return
        print(f"match: {m.home_team_name} vs {m.away_team_name}, sm_id={m.sportmonks_fixture_id}")

        client = SportMonksClient()
        odds = await client.get_odds_pre_match(m.sportmonks_fixture_id)
        print(f"total odds items: {len(odds)}")

        # 只输出 OU (market_id=80) 的记录，看 label/total 的原始结构
        ou_items = [o for o in odds if o.get("market_id") == 80]
        print(f"OU items: {len(ou_items)}")
        # 按 bookmaker 分组，看每家返回的 label 和 total
        from collections import defaultdict
        by_bm = defaultdict(list)
        for o in ou_items:
            by_bm[o.get("bookmaker_id")].append(o)
        for bm_id, items in by_bm.items():
            print(f"\n  bookmaker_id={bm_id} count={len(items)}")
            # 打印每条的 label/total/value（去重）
            seen = set()
            for o in items[:40]:
                label = o.get("label")
                total = o.get("total")
                value = o.get("value")
                key = (label, total)
                if key not in seen:
                    seen.add(key)
                    print(f"    label={label!r} total={total!r} value={value!r}")

asyncio.run(main())
