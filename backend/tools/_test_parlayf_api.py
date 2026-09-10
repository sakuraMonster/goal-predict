# -*- coding: utf-8 -*-
import asyncio
import json
import sys

sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")
from app.api.market_flow import market_flow_parlay_f_recommend
from app.db.database import async_session


async def main():
    async with async_session() as db:
        today = await market_flow_parlay_f_recommend(date="2026-09-09", db=db)
        aug = await market_flow_parlay_f_recommend(start_date="2026-08-01", end_date="2026-08-31", db=db)
    out = []
    out.append("==== 今日 09-09 ====")
    out.append(json.dumps(today, ensure_ascii=False, default=str))
    out.append("\n==== 8月 汇总 ====")
    out.append(json.dumps(aug["stats"], ensure_ascii=False))
    out.append("\n==== 8月 逐日 ====")
    for p in aug["picks"]:
        l1 = p["legs"][0]
        l2 = p["legs"][1]
        out.append(f"{p['matchday']} [{p['combo_level']}] settled={p['settled']} hit={p['hit']} "
                   f"payout={p['payout']} stake={p['stake']} | "
                   f"leg1 {l1['code']} {l1['home_team']}vs{l1['away_team']} played={[(x['key'],x['odds']) for x in l1['played']]} act={l1['actual']} "
                   f"| leg2 {l2['home_team']}vs{l2['away_team']} {l2['pick']}@{l2['odds']} act={l2['actual']}")
    with open(r"e:\zhangxuejun\new-thinking\ricking-03\backend\tools\_parlayf_api_test.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


asyncio.run(main())
print("done")
