"""挪超激进大球调控推演"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

async def main():
    end = datetime(2026,8,7,12,0,0)
    start = end - timedelta(days=30)
    all_data = []
    async with async_session() as db:
        fc = FeatureEngineerB(db); mc = ModelC()
        r = await db.execute(select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time>=start, Match.kickoff_time<end).order_by(Match.kickoff_time))
        for m in r.unique().scalars().all():
            if (m.league.name_zh if m.league else '') != '挪超': continue
            pr = await db.execute(select(Prediction).where(Prediction.match_id==m.id))
            p = pr.scalar_one_or_none()
            if not p or p.actual_total_goals is None: continue
            fd = await fc.extract_features(m.id)
            if fd.empty: continue
            f = fd.iloc[0].to_dict()
            rc = mc.predict(f, '挪超')
            all_data.append({
                'name': f"{m.home_team_name or '?'} vs {m.away_team_name or '?'}",
                'act': p.actual_total_goals, 'lam': rc['expected_goals'],
                'drop': rc['detail']['goal_drop'], 'gl': rc['detail']['goal_line'],
                'hit': p.actual_total_goals in snap_top2(rc['expected_goals']),
                'home_gf': rc['detail']['home_goals_avg'],
                'away_gf': rc['detail']['away_goals_avg'],
            })

    cur_hit = sum(1 for r in all_data if r['hit'])
    print(f"当前: {cur_hit}/{len(all_data)} = {cur_hit/len(all_data)*100:.0f}%\n")

    # 回落分层
    print("回落分层:")
    for lo, hi, lab in [(0, 0.3, '0~0.3'), (0.3, 0.5, '0.3~0.5'), (0.5, 1.0, '0.5~1.0'), 
                          (1.0, 1.5, '1.0~1.5'), (1.5, 99, '1.5+')]:
        sub = [r for r in all_data if lo <= r['drop'] < hi]
        if sub:
            hits = sum(1 for r in sub if r['hit'])
            print(f"  {lab}: {len(sub)}场 HIT={hits} 进球={sorted([r['act'] for r in sub])}")

    # 激进大球调控
    print(f"\n激进调控推演 (0.3 < drop <= 1.0 的场次: λ + boost):")
    target = [r for r in all_data if 0.3 < r['drop'] <= 1.0]
    print(f"  触发场次: {len(target)}")
    for r in target:
        print(f"    {r['name']}: drop={r['drop']:.2f} GL={r['gl']:.2f} λ={r['lam']:.2f} "
              f"实际={r['act']} HIT={'Y' if r['hit'] else 'N'} "
              f"主攻={r['home_gf']:.2f} 客攻={r['away_gf']:.2f}")

    for boost in [0.8, 1.0, 1.2, 1.5, 2.0]:
        nh = 0; gained = 0; lost = 0
        for r in all_data:
            new_lam = r['lam'] + boost if 0.3 < r['drop'] <= 1.0 else r['lam']
            nhit = r['act'] in snap_top2(new_lam)
            if nhit: nh += 1
            if nhit and not r['hit']: gained += 1
            if not nhit and r['hit']: lost += 1
        net = gained - lost
        print(f"  λ+{boost:.1f}: HIT={nh}/{len(all_data)}={nh/len(all_data)*100:.0f}% "
              f"救{gained}丢{lost} 净{net:+d}")

    # 加大触发范围
    print(f"\n扩大范围 (0.3 < drop <= 1.5, 即含边界):")
    for boost in [0.8, 1.0, 1.5]:
        nh = 0; gained = 0; lost = 0
        for r in all_data:
            new_lam = r['lam'] + boost if 0.3 < r['drop'] <= 1.5 else r['lam']
            nhit = r['act'] in snap_top2(new_lam)
            if nhit: nh += 1
            if nhit and not r['hit']: gained += 1
            if not nhit and r['hit']: lost += 1
        net = gained - lost
        print(f"  λ+{boost:.1f}: {nh}/{len(all_data)}={nh/len(all_data)*100:.0f}% 救{gained}丢{lost} 净{net:+d}")

asyncio.run(main())
