"""验证写库结果：葡超 8 场库内 expected_goals_c/snap_top2_c vs 修复后重跑×0.92 期望值"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match


async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match))
            .where(Prediction.match_id.in_([15538, 15540, 15528, 15546, 15556, 15557, 15574, 15575]))
        )
        preds = {p.match_id: p for p in r.unique().scalars().all()}
        # 期望值 = _t4_portugal_detail.py 重跑λ(无calib) × 0.92
        expect = {
            15538: 2.55, 15540: 2.71, 15528: 2.61, 15546: 2.93,
            15556: 2.99, 15557: 2.18, 15574: 3.98, 15575: 2.43,
        }
        ok = True
        for mid, base in expect.items():
            p = preds.get(mid)
            if not p:
                print(f"{mid}: 无 Prediction 记录")
                continue
            exp = base * 0.92
            got = p.expected_goals_c
            match = got is not None and abs(got - exp) < 0.02
            ok = ok and match
            print(f"{mid}: 库内 λ={got} top2={p.snap_top2_c} | 期望 λ={exp:.2f} "
                  f"(重跑{base:.2f}×0.92) {'OK' if match else 'MISMATCH'}")
        print("\n结论:", "全部匹配，写库成功" if ok else "存在不一致，需检查")


asyncio.run(main())
