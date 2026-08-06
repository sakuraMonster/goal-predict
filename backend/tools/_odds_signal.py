"""分析08-02韩K和挪超MISS场次的大小球盘口信号"""
import asyncio, sys
sys.path.insert(0, 'e:/zhangxuejun/new-thinking/ricking-03/backend')

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import OddsSnapshot, Match

async def analyze():
    async with async_session() as db:
        # 08-02 韩K+挪超 MISS场次
        target_ids = [15475, 15476, 15496, 15497, 15498]
        
        for mid in target_ids:
            # 获取比赛
            result = await db.execute(select(Match).where(Match.id == mid))
            match = result.scalar_one()
            
            # 获取赔率
            result = await db.execute(
                select(OddsSnapshot).where(
                    OddsSnapshot.match_id == mid,
                    OddsSnapshot.goal_line.isnot(None)
                ).order_by(OddsSnapshot.snapshot_time.desc())
            )
            odds = result.scalars().all()
            
            print(f"\n{'='*60}")
            print(f"  ID={mid} {match.home_team_name} vs {match.away_team_name}")
            hs = match.home_score or 0
            as_ = match.away_score or 0
            print(f"  比分: {hs}:{as_} (总{hs + as_}球)")
            print(f"{'='*60}")
            
            if not odds:
                print("  (无大小球盘口数据)")
                continue
            
            # 统计goal_line分布
            from collections import Counter
            gl_counter = Counter()
            for o in odds:
                if o.goal_line:
                    gl_counter[o.goal_line] += 1
            
            print(f"  goal_line分布: {dict(gl_counter.most_common())}")
            
            # 最新盘口
            latest = odds[0]
            print(f"  最新盘口: goal_line={latest.goal_line} over={latest.over_odds} under={latest.under_odds} bm={latest.bookmaker}")
            
            # 按bookmaker分组看趋势
            by_bm = {}
            for o in odds:
                if o.bookmaker and o.goal_line:
                    by_bm.setdefault(o.bookmaker, []).append((o.snapshot_time, o.goal_line, o.over_odds, o.under_odds))
            
            print(f"  博彩公司数: {len(by_bm)}")
            
            # Pinnacle是最重要的
            if 'Pinnacle' in by_bm:
                pinnacle = sorted(by_bm['Pinnacle'], key=lambda x: x[0])
                if len(pinnacle) >= 2:
                    first = pinnacle[0]
                    last = pinnacle[-1]
                    print(f"  Pinnacle趋势: {len(pinnacle)}条")
                    print(f"    初: goal_line={first[1]} over={first[2]} under={first[3]}")
                    print(f"    末: goal_line={last[1]} over={last[2]} under={last[3]}")
                    if first[2] and last[2]:
                        over_change = first[2] - last[2]
                        print(f"    over水位变化: {over_change:+.3f} ({'↓看好大球' if over_change < -0.1 else '↑不看好大球' if over_change > 0.1 else '稳定'})")
            
            # 所有bookmaker的goal_line变化
            all_gl_changes = []
            for bm, snaps in by_bm.items():
                snaps_sorted = sorted(snaps, key=lambda x: x[0])
                if len(snaps_sorted) >= 2:
                    gl_change = snaps_sorted[-1][1] - snaps_sorted[0][1]
                    all_gl_changes.append(gl_change)
            
            if all_gl_changes:
                avg_gl_change = sum(all_gl_changes) / len(all_gl_changes)
                print(f"  goal_line平均变动: {avg_gl_change:+.2f}")
                gl_up = sum(1 for c in all_gl_changes if c > 0.1)
                gl_down = sum(1 for c in all_gl_changes if c < -0.1)
                print(f"  盘口上调: {gl_up}家, 下调: {gl_down}家")
            
            # 是否有盘口跳变（>0.5的变动）
            gl_max = max(o.goal_line for o in odds if o.goal_line)
            gl_min = min(o.goal_line for o in odds if o.goal_line)
            gl_range = gl_max - gl_min
            print(f"  盘口范围: {gl_min}~{gl_max} (跨度{gl_range})")
            if gl_range >= 0.5:
                print(f"  ⚠ 盘口波动大，市场分歧明显")

asyncio.run(analyze())
