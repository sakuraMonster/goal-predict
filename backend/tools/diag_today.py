"""深度诊断今天赛事数据"""
import asyncio
from datetime import datetime, date, timedelta
from sqlalchemy import select, text
from app.db.database import async_session
from app.db.models import Match, Team, OddsSnapshot, HeadToHead, TeamSeasonStats, League, TeamAlias, TaskLog

async def main():
    async with async_session() as db:
        today = date.today()
        
        # 查联赛定义
        print("=== 联赛列表 (含欧罗巴相关) ===")
        l_result = await db.execute(
            select(League).where(League.name_zh.ilike('%欧%'))
        )
        for l in l_result.scalars().all():
            print(f"  id={l.id}, name={l.name_zh}, name_en={l.name_en}, sm_id={l.sportmonks_id}, active={l.active}")
        
        # 赛事数据 (扩展范围)
        print("\n=== 08-06 至 08-07 所有赛事 ===")
        result = await db.execute(
            select(Match).where(
                Match.kickoff_time >= datetime(today.year, today.month, today.day),
                Match.kickoff_time < datetime(today.year, today.month, today.day + 2),
            ).order_by(Match.kickoff_time)
        )
        matches = result.scalars().all()
        
        for m in matches:
            ht = await db.get(Team, m.home_team_id) if m.home_team_id else None
            at = await db.get(Team, m.away_team_id) if m.away_team_id else None
            l = await db.get(League, m.league_id) if m.league_id else None
            
            print(f"\n  id={m.id} jc={m.jc_match_id} | {m.kickoff_time}")
            print(f"  联赛: {l.name_zh if l else 'NULL'} (id={m.league_id}, sm={l.sportmonks_id if l else None})")
            print(f"  主: {m.home_team_name} (tid={m.home_team_id}, sm={ht.sportmonks_id if ht else None})")
            print(f"  客: {m.away_team_name} (tid={m.away_team_id}, sm={at.sportmonks_id if at else None})")
            print(f"  SM fixture: {m.sportmonks_fixture_id}, swapped: {m.is_swapped}")
            
            # 赔率统计
            odds_result = await db.execute(
                select(OddsSnapshot).where(OddsSnapshot.match_id == m.id)
            )
            all_odds = odds_result.scalars().all()
            bookmakers = set(o.bookmaker for o in all_odds if o.bookmaker)
            print(f"  赔率记录: {len(all_odds)} 条, 来源: {bookmakers}")
            
            # H2H
            h2h_cnt = await db.execute(
                select(HeadToHead).where(
                    HeadToHead.home_team_id == m.home_team_id,
                    HeadToHead.away_team_id == m.away_team_id,
                )
            )
            h2h_list = h2h_cnt.scalars().all()
            print(f"  H2H: {len(h2h_list)} 条")
            
            # TeamStats
            for side, tid in [('主', m.home_team_id), ('客', m.away_team_id)]:
                stats_r = await db.execute(
                    select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid)
                )
                stats_list = stats_r.scalars().all()
                seasons = [s.season for s in stats_list]
                print(f"  {side}队赛季统计: {len(stats_list)} 条, seasons={seasons}")
        
        # TaskLog
        print("\n=== 最近 TaskLog ===")
        log_result = await db.execute(
            select(TaskLog).order_by(TaskLog.created_at.desc()).limit(8)
        )
        for log in log_result.scalars().all():
            print(f"  [{log.status}] {log.task_type}: {log.message[:150]} | {log.created_at}")

asyncio.run(main())
