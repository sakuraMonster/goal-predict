"""韩K: 将 season=2026 的 recent_matches 合并到 season=2025（不覆盖2025赛季统计）"""
import asyncio, os
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import select, update
from app.db.database import async_session
from app.db.models import TeamSeasonStats

async def main():
    async with async_session() as db:
        # 找韩K(league_id=6) 所有 season=2025 和 season=2026 的记录
        r = await db.execute(
            select(TeamSeasonStats).where(
                TeamSeasonStats.league_id == 6,
                TeamSeasonStats.season.in_(["2025", "2026"]),
            ).order_by(TeamSeasonStats.team_id)
        )
        all_records = list(r.scalars().all())

        # 按 team_id 分组
        by_team = {}
        for s in all_records:
            by_team.setdefault(s.team_id, []).append(s)

        updated = 0
        for tid, records in by_team.items():
            s2025 = None
            s2026 = None
            for s in records:
                if str(s.season) == "2025":
                    s2025 = s
                elif str(s.season) == "2026":
                    s2026 = s

            if not s2025 or not s2026:
                continue

            rm_2026 = s2026.recent_matches
            if not isinstance(rm_2026, list) or len(rm_2026) == 0:
                continue

            # 检查 2025 是否已有 recent_matches
            rm_2025 = s2025.recent_matches
            has_rm = isinstance(rm_2025, list) and len(rm_2025) > 0
            if has_rm:
                print(f'  team_id={tid}: 2025已有{len(rm_2025)}条, 跳过')
                continue

            # 复制 recent_matches 到 2025
            s2025.recent_matches = list(rm_2026)
            updated += 1
            print(f'  team_id={tid}: 2026→2025 复制{len(rm_2026)}条 recent_matches, played={s2025.played}')

        if updated:
            await db.commit()
            print(f'\n已更新 {updated} 条记录')
        else:
            print('\n无需更新')

asyncio.run(main())
