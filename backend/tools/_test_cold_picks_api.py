"""端到端验证 /api/reports/cold-picks 端点（直接调用函数，不经 HTTP）"""
import asyncio
from datetime import datetime, timezone, timedelta

from app.db.database import async_session
from app.api.reports import get_cold_picks, get_cold_picks_history

BEIJING_TZ = timezone(timedelta(hours=8))


async def main():
    now_bj = datetime.now(BEIJING_TZ)
    print(f"现在北京时间: {now_bj:%Y-%m-%d %H:%M}")
    async with async_session() as db:
        # 今天比赛日
        resp = await get_cold_picks(date=None, top_n=3, secondary=3, db=db)
        print(f"\n=== cold-picks (今天) ===")
        print(f"date={resp['date']} candidates={resp['total_candidates']} conflicts={resp['total_conflicts']}")
        for it in resp["data"]:
            print(f"  #{it['match_id']} {it['league_name']} {it['home_team']}vs{it['away_team']} "
                  f"热门={'主胜' if it['fav_dir']==0 else '客胜'}({it['fav_prob']:.0%}) gap={it['rank_gap']} score={it['score']}")

        hist = await get_cold_picks_history(days=30, db=db)
        print(f"\n=== cold-picks/history (30天) ===")
        print(f"summary={hist['summary']}")
        for row in hist["data"][:5]:
            print(f"  {row}")


if __name__ == "__main__":
    asyncio.run(main())
