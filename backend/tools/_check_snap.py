"""对比新旧SNAP规则命中率"""
import asyncio, sys, math
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')
from datetime import datetime
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Prediction

async def main():
    async with async_session() as db:
        start = datetime(2026, 7, 28, 0, 0, 0)
        end = datetime(2026, 8, 4, 0, 0, 0)
        result = await db.execute(
            select(Prediction).where(
                Prediction.kickoff_time >= start,
                Prediction.kickoff_time < end,
            ).order_by(Prediction.kickoff_time)
        )
        preds = list(result.scalars().all())

        old_hit = 0; old_miss = 0
        new_hit = 0; new_miss = 0
        flipped = []

        header = f"{'ID':>6} {'lambda':>6} {'frac':>5} {'SNAP':>5} {'eff':>6} {'新2近':>10} {'旧2近':>10} {'实际':>4} {'新':>4} {'旧':>4} {'flip':>5}  比赛"
        print(header)
        print('-' * len(header))

        for p in preds:
            actual = (p.actual_home_score or 0) + (p.actual_away_score or 0)
            eg = p.expected_goals or 0
            eg_clean = round(eg, 10)  # V4.12 fix: 去浮点噪声
            frac = eg_clean - math.floor(eg_clean)

            # 旧规则: 2 closest to lambda, range(5), cap 4
            old_dists = sorted([(abs(eg - i), i) for i in range(5)])
            old_top2 = {old_dists[0][1], old_dists[1][1]}
            old_capped = min(actual, 4)
            old_is_hit = old_capped in old_top2
            if old_is_hit: old_hit += 1
            else: old_miss += 1

            # 新规则: SNAP + range(5), cap 4
            if frac < 0.10:
                effective = math.floor(eg_clean); snap = 'DOWN'
            elif frac > 0.90:
                effective = math.ceil(eg_clean); snap = 'UP'
            else:
                effective = eg; snap = '-'
            new_dists = sorted([(abs(effective - i), i) for i in range(5)])
            new_top2 = {new_dists[0][1], new_dists[1][1]}
            new_capped = min(actual, 4)
            new_is_hit = new_capped in new_top2
            if new_is_hit: new_hit += 1
            else: new_miss += 1

            if old_is_hit != new_is_hit:
                flipped.append((p.match_id, old_is_hit, new_is_hit, actual, eg, frac, snap))

            flip_mark = '<<<' if old_is_hit != new_is_hit else ''
            n = 'HIT' if new_is_hit else 'MIS'
            o = 'HIT' if old_is_hit else 'MIS'

            match_name = f"{getattr(p, 'home_team_name', '?')} vs {getattr(p, 'away_team_name', '?')}"
            print(f"{p.match_id:>6} {eg:>6.2f} {frac:>5.2f} {snap:>5} {str(effective)[:5]:>6} {str(new_top2):>10} {str(old_top2):>10} {actual:>3}球 {n:>4} {o:>4} {flip_mark:>5}  {match_name}")

        print()
        print(f"旧规则(无SNAP,范围0-4,cap4): {old_hit}/{old_hit+old_miss} = {old_hit/(old_hit+old_miss)*100:.1f}%")
        print(f"新规则(SNAP,  范围0-4,cap4): {new_hit}/{new_hit+new_miss} = {new_hit/(new_hit+new_miss)*100:.1f}%")

        if flipped:
            print(f"\n翻转场次 ({len(flipped)}场):")
            hit2miss = [f for f in flipped if f[1] and not f[2]]
            miss2hit = [f for f in flipped if not f[1] and f[2]]
            print(f"  HIT->MISS ({len(hit2miss)}场):")
            for mid, _, _, act, lam, frac, snap in hit2miss:
                print(f"    ID={mid}  实际={act}球  lambda={lam:.2f}  frac={frac:.2f}  SNAP={snap}")
            print(f"  MISS->HIT ({len(miss2hit)}场):")
            for mid, _, _, act, lam, frac, snap in miss2hit:
                print(f"    ID={mid}  实际={act}球  lambda={lam:.2f}  frac={frac:.2f}  SNAP={snap}")

asyncio.run(main())
