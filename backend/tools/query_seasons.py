"""调试日职联 API 返回"""
import asyncio, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from app.collector.sportmonks.client import SportMonksClient

async def main():
    c = SportMonksClient()
    # 测试不同日期
    for date in ['2025-03-01', '2025-05-15', '2025-08-10']:
        try:
            fixtures = await c.get_fixtures_by_date(date, "participants;scores;league")
            fj1 = [f for f in fixtures if (f.get("league_id") == 968 or (f.get("league", {}) or {}).get("id") == 968)]
            print(f'{date}: total={len(fixtures)}, J1={len(fj1)}')
            if fj1:
                f = fj1[0]
                print(f'  sample: {f.get("name")}, league_id={f.get("league_id")}')
                for p in f.get("participants", []):
                    print(f'    {p.get("name")} loc={(p.get("meta",{})or{}).get("location")}')
        except Exception as e:
            print(f'{date}: ERROR {e}')
    await c.close()

asyncio.run(main())
