import asyncio
from app.collector.sportmonks.client import SportMonksClient

async def main():
    sm = SportMonksClient()
    
    # 拉取 SM 原始赔率
    odds = await sm.get_odds_pre_match(19609655)
    
    # 只取 1X2 (market_id=1) 数据
    spf = [o for o in odds if o.get("market_id") == 1]
    print(f"1X2 odds count: {len(spf)}")
    # 按 bookmaker 取第一家
    seen_bm = set()
    for o in spf:
        bm_id = o.get("bookmaker_id")
        if bm_id not in seen_bm:
            seen_bm.add(bm_id)
            bm_name = (o.get("bookmaker") or {}).get("name", "?")
            print(f"\n{bm_name} (id={bm_id}):")
            # 找该 bookmaker 所有 1X2
            bm_odds = [x for x in spf if x.get("bookmaker_id") == bm_id]
            for x in bm_odds:
                label = x.get("label", "?")
                value = x.get("value")
                print(f"  label={label}, value={value}")
            if len(seen_bm) >= 3:
                break
    
    # 亚盘 (market_id=6)
    hcp = [o for o in odds if o.get("market_id") == 6]
    print(f"\nHandicap odds count: {len(hcp)}")
    seen_bm2 = set()
    for o in hcp:
        bm_id = o.get("bookmaker_id")
        if bm_id not in seen_bm2:
            seen_bm2.add(bm_id)
            bm_name = (o.get("bookmaker") or {}).get("name", "?")
            print(f"\n{bm_name} (id={bm_id}):")
            bm_odds = [x for x in hcp if x.get("bookmaker_id") == bm_id][:5]
            for x in bm_odds:
                label = x.get("label", "?")
                value = x.get("value")
                line = x.get("handicap")
                print(f"  label={label}, value={value}, handicap={line}")
            if len(seen_bm2) >= 2:
                break
    
    await sm.close()

asyncio.run(main())
