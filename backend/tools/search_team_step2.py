"""Step 2: 搜索里莫"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.collector.sportmonks.client import SportMonksClient

async def main():
    sm = SportMonksClient()
    print("=== 里莫 ===", flush=True)
    
    # 先获取竞彩网数据
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
                print(f"竞彩网: {m.get('homeTeamAllName')} vs {m.get('awayTeamAllName')}", flush=True)
                print(f"联赛: {m.get('leagueAllName')} ({m.get('leagueAbbName')})", flush=True)
                print(f"主场缩写: {m.get('homeTeamAbbEnName')} 客场缩写: {m.get('awayTeamAbbEnName')}", flush=True)
                
                # 对手是米拉索尔 (sm_id=11126)
                # 用米拉索尔的 fixture 来查
                home_abb = m.get('homeTeamAbbEnName', '')
                away_abb = m.get('awayTeamAbbEnName', '')
                
                # 搜索对手
                search_terms = []
                if away_abb:
                    search_terms.append(away_abb)
                search_terms.extend(["Remo", "Remo PA", "Clube do Remo", "Rimo"])
                
                for term in search_terms:
                    print(f"\n搜索 '{term}'...", flush=True)
                    try:
                        results = await sm.search_teams(term)
                        if results:
                            for r in results[:5]:
                                print(f"  id={r.get('id')} name={r.get('name')} short={r.get('short_code')} country={r.get('country')}", flush=True)
                    except Exception as e:
                        print(f"  失败: {e}", flush=True)
                
                # 用日期查米拉索尔的比赛
                print(f"\n通过日期查 07-30 米拉索尔(11126)的比赛:", flush=True)
                try:
                    fixtures_data = await sm._get(
                        "/fixtures/date/2026-07-30",
                        {"include": "participants"}
                    )
                    fixtures = fixtures_data.get("data", [])
                    print(f"共 {len(fixtures)} 场", flush=True)
                    for f in fixtures:
                        parts = f.get("participants", [])
                        ids = [p.get('id') for p in parts]
                        if 11126 in ids:
                            print(f"  fixture={f.get('id')} {f.get('name')}", flush=True)
                            for p in parts:
                                print(f"    id={p.get('id')} name={p.get('name')} short={p.get('short_code')}", flush=True)
                except Exception as e:
                    print(f"  失败: {e}", flush=True)

    await sm.close()

asyncio.run(main())
