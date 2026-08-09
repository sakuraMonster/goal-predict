"""检查今天赛事数据完整性"""
import asyncio
from datetime import datetime, date
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Team, OddsSnapshot, HeadToHead, TeamSeasonStats, League, TaskLog

async def main():
    async with async_session() as db:
        today = date.today()
        
        result = await db.execute(
            select(Match).where(
                Match.kickoff_time >= datetime(today.year, today.month, today.day),
                Match.kickoff_time < datetime(today.year, today.month, today.day + 1),
            ).order_by(Match.kickoff_time)
        )
        matches = result.scalars().all()
        print(f'=== 今天赛事 ({len(matches)} 场) ===')
        
        for m in matches:
            l_result = await db.execute(select(League).where(League.id == m.league_id))
            league = l_result.scalar_one_or_none()
            league_name = league.name_zh if league else '?'
            league_sm_id = league.sportmonks_id if league else None
            
            ht = await db.get(Team, m.home_team_id) if m.home_team_id else None
            at = await db.get(Team, m.away_team_id) if m.away_team_id else None
            
            print(f'\n--- 赛事 id={m.id} jc_id={m.jc_match_id} ---')
            print(f'  时间: {m.kickoff_time}')
            print(f'  联赛: {league_name} (league_id={m.league_id}, sm_league_id={league_sm_id})')
            print(f'  主队: {m.home_team_name} (team_id={m.home_team_id}, sm_id={ht.sportmonks_id if ht else None}, name_en={ht.name_en if ht else None})')
            print(f'  客队: {m.away_team_name} (team_id={m.away_team_id}, sm_id={at.sportmonks_id if at else None}, name_en={at.name_en if at else None})')
            print(f'  SM fixture_id: {m.sportmonks_fixture_id}')
            print(f'  is_swapped: {m.is_swapped}')
            
            # 赔率
            odds_result = await db.execute(
                select(OddsSnapshot).where(OddsSnapshot.match_id == m.id).order_by(OddsSnapshot.snapshot_time.desc()).limit(1)
            )
            odds = odds_result.scalar_one_or_none()
            if odds:
                print(f'  赔率: home_win={odds.home_win}, draw={odds.draw}, away_win={odds.away_win}')
                print(f'  让球: handicap_home={odds.handicap_home}, handicap_line={odds.handicap_line}, handicap_away={odds.handicap_away}')
                print(f'  盘口: bookmaker={odds.bookmaker}, is_opening={odds.is_opening}')
            else:
                print(f'  赔率: 无')
            
            # H2H
            h2h_result = await db.execute(
                select(HeadToHead).where(
                    HeadToHead.home_team_id == m.home_team_id,
                    HeadToHead.away_team_id == m.away_team_id,
                ).limit(5)
            )
            h2h = h2h_result.scalars().all()
            print(f'  H2H 记录数: {len(h2h)}')
            
            # 球队赛季统计
            for side, tid in [('主', m.home_team_id), ('客', m.away_team_id)]:
                stats_result = await db.execute(
                    select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid).order_by(TeamSeasonStats.season.desc()).limit(1)
                )
                stats = stats_result.scalar_one_or_none()
                if stats:
                    print(f'  {side}队赛季统计: season={stats.season}, played={stats.played}, wins={stats.wins}, goals_for={stats.goals_for}')
                else:
                    print(f'  {side}队赛季统计: 无')
        
        # 最近 TaskLog
        print('\n=== 最近 Pipeline 执行记录 ===')
        log_result = await db.execute(
            select(TaskLog).order_by(TaskLog.created_at.desc()).limit(10)
        )
        for log in log_result.scalars().all():
            print(f'  [{log.status}] {log.task_name}: {log.message[:120]} ({log.created_at})')

asyncio.run(main())
