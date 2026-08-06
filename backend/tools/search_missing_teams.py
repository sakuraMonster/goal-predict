"""查找 波兹南莱赫 和 里莫 的 SportMonks 映射"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.collector.sportmonks.client import SportMonksClient

async def main():
    sm = SportMonksClient()

    # ============ 1. 波兹南莱赫 ============
    print("=== 波兹南莱赫 ===")
    # 尝试多种搜索
    for term in ["Lech Poznan", "Lech", "Poznan"]:
        try:
            results = await sm.search_teams(term)
            if results:
                print(f"  搜索 '{term}' 结果:")
                for r in results[:3]:
                    print(f"    id={r.get('id')} name={r.get('name')} short={r.get('short_code')} country={r.get('country')}")
        except Exception as e:
            print(f"  搜索 '{term}' 失败: {e}")

    # 通过 fixture 19721239 找主队
    try:
        fx = await sm.get_fixture_by_id(19721239, includes="participants")
        participants = fx.get("participants", [])
        print(f"\n  Fixture 19721239 参赛队:")
        for p in participants:
            print(f"    id={p.get('id')} name={p.get('name')} short={p.get('short_code')} meta={p.get('meta')}")
    except Exception as e:
        print(f"  Fixture 查询失败: {e}")

    # ============ 2. 里莫 ============
    print("\n=== 里莫 ===")
    # 尝试直接搜索
    for term in ["Rimo", "Limo", "Remo"]:
        try:
            results = await sm.search_teams(term)
            if results:
                print(f"  搜索 '{term}' 结果:")
                for r in results[:3]:
                    print(f"    id={r.get('id')} name={r.get('name')} short={r.get('short_code')} country={r.get('country')}")
        except Exception as e:
            print(f"  搜索 '{term}' 失败: {e}")

    # 里莫的对手是米拉索尔 (sm_id=11126)，根据竞彩网数据找这场比赛
    # jc_match_id=2040647, 07-30 06:30, 米拉索尔 vs 里莫 (里莫是客队)
    # 搜索米拉索尔 07-30 附近的比赛
    print("\n  通过米拉索尔 (11126) 查找 07-30 比赛:")
    try:
        # 获取米拉索尔的 fixtures
        from datetime import datetime
        fixtures_data = await sm._get(
            f"/fixtures/date/{'2026-07-30'}",
            {"include": "participants"}
        )
        fixtures = fixtures_data.get("data", [])
        print(f"  07-30 共 {len(fixtures)} 场比赛")
        for f in fixtures:
            parts = f.get("participants", [])
            names = []
            ids = []
            for p in parts:
                names.append(f"{p.get('name')}({p.get('id')})")
                ids.append(p.get('id'))
            # 检查是否有米拉索尔或里莫相关
            if 11126 in ids:
                print(f"    找到米拉索尔比赛: fixture_id={f.get('id')} {f.get('name')} {' vs '.join(names)}")
    except Exception as e:
        print(f"  Fixture 搜索失败: {e}")

    # 也尝试通过竞彩网的 jc_match_id 反向找 fixture
    # 先通过竞彩网获取这场比赛的信息
    print("\n  尝试通过竞彩网 jc_match_id=2040647 找 fixture:")
    try:
        import httpx
        url = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry?poolCode=hhad,had&channel=c"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.sporttery.cn/jc/jsq/zqspf/",
        }
        r = httpx.get(url, headers=headers, timeout=15)
        data = r.json()
        match_list = data.get("value", {}).get("matchInfoList", [])
        for day_group in match_list:
            for m in day_group.get("subMatchList", []):
                if m.get("matchId") == "2040647":
                    print(f"    竞彩网: {m.get('homeTeamAllName')} vs {m.get('awayTeamAllName')}")
                    print(f"    时间: {m.get('matchDate')} {m.get('matchTime')}")
                    print(f"    联赛: {m.get('leagueAllName')} ({m.get('leagueAbbName')})")
                    print(f"    homeTeamId={m.get('homeTeamId')} awayTeamId={m.get('awayTeamId')}")
                    # 用联赛+队名去搜
                    league_name = m.get('leagueAllName', '')
                    home_name = m.get('homeTeamAllName', '')
                    away_name = m.get('awayTeamAllName', '')
                    print(f"    主队英文缩写: {m.get('homeTeamAbbEnName')} 客队英文缩写: {m.get('awayTeamAbbEnName')}")
    except Exception as e:
        print(f"    竞彩网查询失败: {e}")

    await sm.close()

asyncio.run(main())
