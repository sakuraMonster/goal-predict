"""从 SportMonks 同步 xG/xGA（已验证 type_id=5304）"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import select, distinct
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.db.database import engine
from app.db.models import TeamSeasonStats, Team, Match
from app.collector.sportmonks.client import SportMonksClient
from datetime import datetime, timedelta

async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    sm = SportMonksClient()

    async with sf() as db:
        cutoff = datetime.utcnow() - timedelta(days=60)
        r = await db.execute(
            select(distinct(Match.home_team_id)).where(Match.kickoff_time >= cutoff, Match.home_team_id.isnot(None))
            .union(select(distinct(Match.away_team_id)).where(Match.kickoff_time >= cutoff, Match.away_team_id.isnot(None)))
        )
        active_ids = {row[0] for row in r}
        r2 = await db.execute(select(Team).where(Team.id.in_(list(active_ids)), Team.sportmonks_id.isnot(None)))
        teams = list(r2.scalars().all())
        print(f"球队: {len(teams)}")

        updated = 0; no_data = 0; failed = 0

        for i, team in enumerate(teams):
            sm_id = team.sportmonks_id
            if (i + 1) % 10 == 0:
                print(f"  {i+1}/{len(teams)} (ok{updated} nodata{no_data} fail{failed})")

            try:
                # 用 season_id=0 获取所有赛季（已验证可用）
                all_seasons = await sm.get_statistics_by_season_team(0, sm_id)
            except Exception:
                failed += 1
                continue

            if not isinstance(all_seasons, list) or not all_seasons:
                no_data += 1
                continue

            # 从第一个有 xG 数据的赛季提取
            xg_val = None
            for ss in all_seasons:
                if not isinstance(ss, dict): continue
                details = ss.get("details", [])
                for d in details:
                    if not isinstance(d, dict): continue
                    if d.get("type_id") != 5304: continue
                    val = d.get("value", {})
                    if isinstance(val, dict) and "expected" in val:
                        xg_val = float(val["expected"])
                        break
                if xg_val is not None:
                    break

            if xg_val is None:
                no_data += 1
                continue

            # 更新该球队所有 records
            r3 = await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id == team.id))
            for ts in r3.scalars():
                ts.xG = xg_val  # 赛季总 xG
            updated += 1

        await db.flush()
        await db.commit()
    await sm.close()
    print(f"\n完成: 更新={updated}, 无数据={no_data}, 失败={failed}")

asyncio.run(main())
