"""MISS场次深度分析：按错误类型分类，找优化方向"""
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

        misses = []
        for p in preds:
            actual = (p.actual_home_score or 0) + (p.actual_away_score or 0)
            eg = p.expected_goals or 0
            frac = eg - math.floor(eg)
            # SNAP rule
            if frac < 0.10:
                effective = math.floor(eg)
            elif frac > 0.90:
                effective = math.ceil(eg)
            else:
                effective = eg
            dists = sorted([(abs(effective - i), i) for i in range(5)])
            top2 = {dists[0][1], dists[1][1]}
            act_capped = min(actual, 4)
            is_hit = act_capped in top2

            if not is_hit:
                raw_lambda = p.expected_goals  # from DB
                # Get raw lambda before calibration
                raw_lam = raw_lambda  # DB stores final lambda
                
                high = eg - actual  # 正值=高估, 负值=低估
                misses.append({
                    'id': p.match_id,
                    'match': f"{getattr(p, 'home_team_name', '?')} vs {getattr(p, 'away_team_name', '?')}",
                    'actual': actual,
                    'lambda': eg,
                    'frac': frac,
                    'top2': top2,
                    'high': high,
                    'snap': 'DOWN' if frac < 0.10 else 'UP' if frac > 0.90 else '-',
                    'actual_home': p.actual_home_score or 0,
                    'actual_away': p.actual_away_score or 0,
                })

        # Categorize
        over_estimate = [m for m in misses if m['high'] > 0.5]  # 高估>0.5球
        under_estimate = [m for m in misses if m['high'] < -0.5]  # 低估>0.5球
        near_miss = [m for m in misses if abs(m['high']) <= 0.5]  # 接近但偏了

        print(f"=== MISS 场次分析 (SNAP规则) ===")
        print(f"总MISS: {len(misses)}/{len(preds)}\n")

        print(f"--- 高估型 ({len(over_estimate)}场): λ 比实际高 >0.5球 ---")
        for m in sorted(over_estimate, key=lambda x: -x['high']):
            snap_info = f" SNAP={m['snap']}(eff={math.floor(m['lambda']) if m['snap']=='DOWN' else math.ceil(m['lambda']) if m['snap']=='UP' else m['lambda']:.1f})"
            print(f"  ID={m['id']:5d} 实际={m['actual']}球  λ={m['lambda']:.2f}  高估{m['high']:.1f}球  2近={m['top2']}{snap_info}  {m['match']}")

        print(f"\n--- 低估型 ({len(under_estimate)}场): λ 比实际低 >0.5球 ---")
        for m in sorted(under_estimate, key=lambda x: x['high']):
            snap_info = f" SNAP={m['snap']}(eff={math.floor(m['lambda']) if m['snap']=='DOWN' else math.ceil(m['lambda']) if m['snap']=='UP' else m['lambda']:.1f})"
            print(f"  ID={m['id']:5d} 实际={m['actual']}球  λ={m['lambda']:.2f}  低估{abs(m['high']):.1f}球  2近={m['top2']}{snap_info}  {m['match']}")

        print(f"\n--- 边缘MISS ({len(near_miss)}场): 偏差≤0.5球 ---")
        for m in sorted(near_miss, key=lambda x: abs(x['high'])):
            snap_info = f" SNAP={m['snap']}(eff={math.floor(m['lambda']) if m['snap']=='DOWN' else math.ceil(m['lambda']) if m['snap']=='UP' else m['lambda']:.1f})"
            print(f"  ID={m['id']:5d} 实际={m['actual']}球  λ={m['lambda']:.2f}  偏差{m['high']:+.1f}球  2近={m['top2']}{snap_info}  {m['match']}")

        # Deep dive: 高估型中哪些是零进球?
        zero_goals = [m for m in over_estimate if m['actual'] == 0]
        print(f"\n--- 零进球高估 ({len(zero_goals)}场) ---")
        for m in sorted(zero_goals, key=lambda x: -x['high']):
            print(f"  ID={m['id']:5d}  λ={m['lambda']:.2f}  高估{m['high']:.1f}球  2近={m['top2']}  {m['match']}")

        # 大比分低估
        big_scores = [m for m in under_estimate if m['actual'] >= 4]
        print(f"\n--- 大比分低估 (实际≥4球, {len(big_scores)}场) ---")
        for m in sorted(big_scores, key=lambda x: x['high']):
            print(f"  ID={m['id']:5d} 实际={m['actual']}球 ({m['actual_home']}:{m['actual_away']})  λ={m['lambda']:.2f}  低估{abs(m['high']):.1f}球  {m['match']}")

        # SNAP相关: frac接近阈值的
        near_snap = [m for m in misses if 0.08 <= m['frac'] <= 0.12 or 0.88 <= m['frac'] <= 0.92]
        print(f"\n--- 接近SNAP阈值 (frac 0.08-0.12 或 0.88-0.92, {len(near_snap)}场) ---")
        for m in sorted(near_snap, key=lambda x: x['frac']):
            print(f"  ID={m['id']:5d}  λ={m['lambda']:.2f}  frac={m['frac']:.3f}  SNAP={m['snap']}  实际={m['actual']}球  2近={m['top2']}  {m['match']}")

        # Summary stats
        total = len(preds)
        hit = total - len(misses)
        print(f"\n=== 汇总 ===")
        print(f"命中率: {hit}/{total} = {hit/total*100:.1f}%")
        print(f"MISS分布: 高估{len(over_estimate)} + 低估{len(under_estimate)} + 边缘{len(near_miss)}")
        print(f"零进球MISS: {len(zero_goals)}场 (占MISS {len(zero_goals)/len(misses)*100:.0f}%)")
        print(f"需提升到60%: 需多命中 {int(total*0.6) - hit} 场")

asyncio.run(main())
