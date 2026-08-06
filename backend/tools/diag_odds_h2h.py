"""深度诊断：无赔率比赛 & 缺失H2H的根因分析"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import date, datetime, timedelta
from app.db.database import async_session
from app.db.models import Match, Team, OddsSnapshot, HeadToHead, TeamSeasonStats
from app.collector.sportmonks.client import SportMonksClient
from sqlalchemy import select, func, or_
import json


async def main():
    today = date(2026, 8, 1)
    tomorrow = date(2026, 8, 2)

    async with async_session() as db:
        # ====== 1. 获取所有比赛 ======
        result = await db.execute(
            select(Match).where(
                func.date(Match.kickoff_time).in_([today, tomorrow]),
            ).order_by(Match.kickoff_time)
        )
        matches = list(result.scalars().all())
        print(f"总比赛数: {len(matches)}")
        print(f"{'='*70}")

        # ====== 2. 分析无赔率比赛 ======
        no_odds_matches = []
        has_odds_matches = []

        for m in matches:
            odds_count = await db.scalar(
                select(func.count(OddsSnapshot.id)).where(OddsSnapshot.match_id == m.id)
            ) or 0
            if odds_count == 0:
                no_odds_matches.append(m)
            else:
                has_odds_matches.append(m)

        print(f"\n{'='*70}")
        print(f"【赔率分析】有赔率: {len(has_odds_matches)}场, 无赔率: {len(no_odds_matches)}场")
        print(f"{'='*70}")

        print(f"\n--- 无赔率比赛详情 ---")
        for m in no_odds_matches:
            home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"

            # 检查关键字段
            sm_fixture = m.sportmonks_fixture_id
            has_home_sm = m.home_team.sportmonks_id if m.home_team else None
            has_away_sm = m.away_team.sportmonks_id if m.away_team else None
            is_swapped = m.is_swapped

            print(f"\n  ID={m.id} | {m.kickoff_time.strftime('%m-%d %H:%M') if m.kickoff_time else '??'} | {home} vs {away}")
            print(f"    SM fixture_id: {sm_fixture or '❌ 缺失'}")
            print(f"    home SM id: {has_home_sm or '❌ 缺失'}, away SM id: {has_away_sm or '❌ 缺失'}")
            print(f"    is_swapped: {is_swapped}")
            print(f"    home_team_id={m.home_team_id}, away_team_id={m.away_team_id}")
            print(f"    league_id={m.league_id}")

        # ====== 3. 按联赛/类型分组 ======
        print(f"\n\n--- 无赔率比赛按原始队名分组 ---")
        leagues_no_odds = {}
        for m in no_odds_matches:
            home_name = m.home_team_name or "?"
            away_name = m.away_team_name or "?"
            key = f"{home_name} vs {away_name}"
            league_name = m.league.name_zh if m.league else "?"
            if league_name not in leagues_no_odds:
                leagues_no_odds[league_name] = []
            leagues_no_odds[league_name].append((m.id, key, m.sportmonks_fixture_id, m.kickoff_time))

        for league, items in sorted(leagues_no_odds.items()):
            print(f"\n  {league} ({len(items)}场):")
            for mid, key, sm_fix, kt in items:
                print(f"    ID={mid} | {kt.strftime('%m-%d %H:%M') if kt else '?'} | {key} | SM fixture={sm_fix}")

        # ====== 4. 检查有赔率的比赛的共同特征 ======
        print(f"\n\n--- 有赔率比赛（对比组）---")
        for m in has_odds_matches:
            home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
            print(f"  ID={m.id} | SM fixture={m.sportmonks_fixture_id} | {home} vs {away}")

        # ====== 5. 分析缺失H2H的比赛 ======
        print(f"\n{'='*70}")
        print(f"【H2H分析】")
        print(f"{'='*70}")

        for m in matches:
            if not m.home_team_id or not m.away_team_id:
                continue
            h2h_count = await db.scalar(
                select(func.count(HeadToHead.id)).where(
                    ((HeadToHead.home_team_id == m.home_team_id) & (HeadToHead.away_team_id == m.away_team_id)) |
                    ((HeadToHead.home_team_id == m.away_team_id) & (HeadToHead.away_team_id == m.home_team_id))
                )
            ) or 0

            if h2h_count == 0:
                home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
                away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
                home_sm = m.home_team.sportmonks_id if m.home_team else None
                away_sm = m.away_team.sportmonks_id if m.away_team else None
                print(f"\n  ID={m.id} | {home} vs {away} | 无H2H")
                print(f"    home_team_id={m.home_team_id} (SM={home_sm}), away_team_id={m.away_team_id} (SM={away_sm})")

                # 尝试直接查 SportMonks H2H API
                if home_sm and away_sm:
                    print(f"    → 尝试 SM H2H API: team1={home_sm}, team2={away_sm}...")
                    try:
                        sm = SportMonksClient()
                        h2h_data = await sm.get_head_to_head(home_sm, away_sm)
                        if h2h_data:
                            print(f"    ✓ SM API 返回 {len(h2h_data)} 条交锋记录")
                            for h in h2h_data[:3]:
                                print(f"      - {h.get('name','?')}: {h.get('starting_at','?')}")
                        else:
                            print(f"    ✗ SM API 返回空（可能确实没有历史交锋）")
                    except Exception as e:
                        print(f"    ✗ SM API 调用失败: {e}")


if __name__ == "__main__":
    asyncio.run(main())
