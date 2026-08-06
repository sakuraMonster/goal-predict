"""直接查数据库"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Team, HeadToHead, TeamSeasonStats, OddsSnapshot

async def main():
    async with async_session() as db:
        for mid in [15502, 15503, 15504, 15505]:
            m = (await db.execute(select(Match).where(Match.id == mid))).scalar_one_or_none()
            if not m:
                print(f"ID={mid}: DB无记录")
                continue
            
            ht = m.home_team
            at = m.away_team
            lg = m.league
            
            print(f"\nID={mid}  {ht.name_zh if ht else m.home_team_name}(SM={ht.sportmonks_id if ht else '?'}) vs {at.name_zh if at else m.away_team_name}(SM={at.sportmonks_id if at else '?'})")
            print(f"  match_num={m.match_num}  league={lg.name_zh if lg else '?'}")
            
            # H2H
            h2h_result = await db.execute(
                select(HeadToHead).where(
                    ((HeadToHead.home_team_id == m.home_team_id) & (HeadToHead.away_team_id == m.away_team_id)) |
                    ((HeadToHead.home_team_id == m.away_team_id) & (HeadToHead.away_team_id == m.home_team_id))
                )
            )
            h2h_list = h2h_result.scalars().all()
            print(f"  H2H: {len(h2h_list)}条")
            for h in h2h_list[:3]:
                print(f"    {h.match_date} {h.home_score}-{h.away_score} (team_ids: {h.home_team_id}-{h.away_team_id})")
            
            # Form
            for tid, side, tname in [
                (m.home_team_id, "主队", ht.name_zh if ht else m.home_team_name),
                (m.away_team_id, "客队", at.name_zh if at else m.away_team_name)
            ]:
                result = await db.execute(
                    select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid).order_by(TeamSeasonStats.id.desc()).limit(1)
                )
                stats = result.scalar_one_or_none()
                form = stats.form if stats else None
                recent = stats.recent_matches if stats else None
                print(f"  {side}({tname}) form={form} recent={len(recent) if recent else 0}场")
            
            # Odds
            odds_result = await db.execute(select(OddsSnapshot).where(OddsSnapshot.match_id == mid))
            odds_list = odds_result.scalars().all()
            print(f"  Odds: {len(odds_list)}条")
            if odds_list:
                companies = set(o.bookmaker for o in odds_list)
                print(f"    公司: {companies}")
                for o in odds_list[:2]:
                    print(f"    {o.bookmaker}: {o.home_win}/{o.draw}/{o.away_win}")

asyncio.run(main())
