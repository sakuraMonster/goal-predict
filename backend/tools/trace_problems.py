"""追溯5场问题比赛的数据链路"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from app.db.database import async_session
from app.db.models import Match, Team, TeamAlias
from app.collector.sportmonks.client import SportMonksClient
from sqlalchemy import select

PROBLEM_IDS = [15483, 15486, 15494, 15495, 15496]


async def main():
    async with async_session() as db:
        for mid in PROBLEM_IDS:
            result = await db.execute(select(Match).where(Match.id == mid))
            m = result.scalar_one_or_none()
            if not m:
                print(f"ID={mid}: 不存在")
                continue

            print(f"\n{'='*70}")
            print(f"ID={mid} | {m.home_team_name} vs {m.away_team_name}")
            print(f"  kickoff: {m.kickoff_time}")
            print(f"  jc_match_id: {m.jc_match_id}")
            print(f"  match_num: {m.match_num}")
            print(f"  SM fixture: {m.sportmonks_fixture_id or 'NONE'}")
            print(f"  is_swapped: {m.is_swapped}")

            # 主队
            print(f"\n  主队 (team_id={m.home_team_id}):")
            if m.home_team:
                t = m.home_team
                print(f"    name_zh: {t.name_zh}")
                print(f"    name_en: {t.name_en}")
                print(f"    SM id: {t.sportmonks_id}")
                print(f"    needs_review: {t.needs_review}")
                print(f"    review_reason: {t.review_reason}")
                print(f"    logo_url: {t.logo_url}")
                # 查别名
                aliases = await db.execute(
                    select(TeamAlias).where(TeamAlias.team_id == m.home_team_id)
                )
                alias_list = aliases.scalars().all()
                if alias_list:
                    for a in alias_list:
                        print(f"    alias: {a.alias_name} (league={a.league_name_zh})")
            else:
                print(f"    ❌ 无关联Team")

            # 客队
            print(f"\n  客队 (team_id={m.away_team_id}):")
            if m.away_team:
                t = m.away_team
                print(f"    name_zh: {t.name_zh}")
                print(f"    name_en: {t.name_en}")
                print(f"    SM id: {t.sportmonks_id}")
                print(f"    needs_review: {t.needs_review}")
                print(f"    review_reason: {t.review_reason}")
                print(f"    logo_url: {t.logo_url}")
                aliases = await db.execute(
                    select(TeamAlias).where(TeamAlias.team_id == m.away_team_id)
                )
                alias_list = aliases.scalars().all()
                if alias_list:
                    for a in alias_list:
                        print(f"    alias: {a.alias_name} (league={a.league_name_zh})")
            else:
                print(f"    ❌ 无关联Team")

    # 对3场SM ID可能错误的客队做SM API验证
    print(f"\n{'='*70}")
    print("SM API 验证: 搜索客队")
    sm = SportMonksClient()

    # 坦佩雷山猫 (SM=8998)
    print("\n--- 坦佩雷山猫 (SM=8998) ---")
    team_data = await sm.get_team_by_id(8998)
    if team_data:
        print(f"  SM name: {team_data.get('name')}")
        print(f"  SM country: {team_data.get('country',{}).get('name','?') if isinstance(team_data.get('country'), dict) else '?'}")
    else:
        print(f"  ❌ SM返回空")

    # 厄尔格里特 (SM=86)  
    print("\n--- 厄尔格里特 (SM=86) ---")
    team_data = await sm.get_team_by_id(86)
    if team_data:
        print(f"  SM name: {team_data.get('name')}")
        print(f"  SM country: {team_data.get('country',{}).get('name','?') if isinstance(team_data.get('country'), dict) else '?'}")

    # 克里斯蒂 (SM=869)
    print("\n--- 克里斯蒂 (SM=869) ---")
    team_data = await sm.get_team_by_id(869)
    if team_data:
        print(f"  SM name: {team_data.get('name')}")
        print(f"  SM country: {team_data.get('country',{}).get('name','?') if isinstance(team_data.get('country'), dict) else '?'}")

    # 搜索这些队名
    for search_name in ["Kristiansund", "Kristiansund BK", "Orgryte", "Orgryte IS", "Ilves Tampere", "Tampere"]:
        print(f"\n--- SM搜索: '{search_name}' ---")
        results = await sm.search_teams(search_name)
        for r in results[:3]:
            print(f"  id={r.get('id')} name={r.get('name')} country={r.get('country',{}).get('name','?') if isinstance(r.get('country'), dict) else '?'}")

    await sm.close()


if __name__ == "__main__":
    asyncio.run(main())
