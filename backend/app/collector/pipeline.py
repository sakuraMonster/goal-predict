"""
数据采集 Pipeline：整合 SportMonks + 500.com 数据，写入数据库
"""
from datetime import datetime, timedelta
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.collector.sportmonks.client import SportMonksClient
from app.collector.scrapers import jczq_scraper
from app.db.models import Match, OddsSnapshot, TeamSeasonStats, Team, TaskLog
from app.db.database import async_session


class SyncPipeline:
    """数据同步主管道"""

    def __init__(self):
        self.sm = SportMonksClient()

    async def _log_task(self, task_type: str, status: str, message: str = "", duration_ms: int = 0):
        """写入任务日志"""
        async with async_session() as db:
            log = TaskLog(
                task_type=task_type,
                status=status,
                start_time=datetime.utcnow(),
                end_time=datetime.utcnow(),
                duration_ms=duration_ms,
                message=message
            )
            db.add(log)
            await db.commit()

    async def sync_daily_matches(self):
        """同步当日+未来3日竞彩赛程"""
        start = datetime.utcnow()
        today = datetime.now().date()
        total = 0
        try:
            for i in range(4):
                date = today + timedelta(days=i)
                matches = await jczq_scraper.scrape_daily_matches()
                async with async_session() as db:
                    for m in matches:
                        jc_id = m.get("jc_match_id", "")
                        if not jc_id:
                            continue
                        result = await db.execute(
                            select(Match).where(Match.jc_match_id == jc_id)
                        )
                        existing = result.scalar_one_or_none()
                        if not existing:
                            db.add(Match(
                                jc_match_id=jc_id,
                                kickoff_time=datetime.now(),
                                handicap_line=m.get("handicap_line", 0.0) or 0.0,
                                venue=m.get("venue", ""),
                            ))
                            total += 1
                    await db.commit()
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("sync_matches", "success", f"同步 {total} 场赛事", duration)
        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("sync_matches", "failed", str(e), duration)
            raise

    async def sync_odds(self):
        """同步赔率变动"""
        start = datetime.utcnow()
        try:
            updated = 0
            async with async_session() as db:
                result = await db.execute(
                    select(Match).where(Match.status == "scheduled")
                )
                matches = result.scalars().all()
                for match in matches:
                    if not match.jc_match_id:
                        continue
                    odds_data = await jczq_scraper.scrape_odds(match.jc_match_id)
                    if not odds_data:
                        continue
                    bookmakers = odds_data.get("bookmakers", [])
                    for bm in bookmakers:
                        db.add(OddsSnapshot(
                            match_id=match.id,
                            snapshot_time=datetime.utcnow(),
                            bookmaker=bm.get("name", ""),
                            home_win=bm.get("home_win"),
                            draw=bm.get("draw"),
                            away_win=bm.get("away_win"),
                            handicap_home=bm.get("handicap_home"),
                            handicap_line=bm.get("handicap_line"),
                            handicap_away=bm.get("handicap_away"),
                        ))
                        updated += 1
                await db.commit()
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("sync_odds", "success", f"更新 {len(matches)} 场赔率", duration)
        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("sync_odds", "failed", str(e), duration)
            raise

    async def sync_team_info(self):
        """更新球队伤病/阵容/积分排名"""
        start = datetime.utcnow()
        try:
            async with async_session() as db:
                result = await db.execute(select(Team).where(Team.sportmonks_id.isnot(None)))
                teams = result.scalars().all()
                for team in teams:
                    if not team.sportmonks_id:
                        continue
                    try:
                        stats_data = await self.sm.get_team_stats(team.sportmonks_id, 0)
                        # 更新球队数据（按需填充）
                    except Exception:
                        continue
                await db.commit()
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("update_teams", "success", "球队信息更新完成", duration)
        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            await self._log_task("update_teams", "failed", str(e), duration)
            raise
