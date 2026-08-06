"""修复损坏的开赛时间：从竞彩网重新拉取并更新"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.collector.scrapers import jczq_scraper
from app.db.database import async_session
from sqlalchemy import select, update
from app.db.models import Match
from datetime import datetime

async def main():
    # 从竞彩网拉取最新赛程
    matches = await jczq_scraper.scrape_daily_matches()
    print(f"竞彩网返回 {len(matches)} 场赛事:")
    for m in matches:
        print(f"  jc_id={m['jc_match_id']} kickoff={m['kickoff_time']} {m['home_team']} vs {m['away_team']}")

    # 更新数据库中的开赛时间
    async with async_session() as db:
        for m in matches:
            jc_id = m.get("jc_match_id", "")
            kickoff_str = m.get("kickoff_time", "")
            if not jc_id or not kickoff_str:
                continue
            try:
                kickoff = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    kickoff = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M")
                except ValueError:
                    continue

            await db.execute(
                update(Match)
                .where(Match.jc_match_id == jc_id)
                .values(kickoff_time=kickoff, home_team_name=m.get("home_team", ""), away_team_name=m.get("away_team", ""))
            )

        await db.commit()
        print("\n数据库已更新")

asyncio.run(main())
