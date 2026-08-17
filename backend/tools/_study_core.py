"""核心实证: 严格口径(over+under 齐全主盘线) 52 场
实证一: 开盘线 vs 收盘线(开球前最后时刻) 谁更接近实际进球
实证二: 盘口变动方向与最终进球关系(跟随 vs 反转)
"""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot

START = datetime(2026, 7, 11, 12, 0, 0)
END = datetime(2026, 8, 11, 12, 0, 0)


def main_line(records):
    """records: [(gl, ov, un, bm)] → (主盘线众数, {gl: 公司数})"""
    bm_main = {}
    for gl, ov, un, bm in records:
        if ov is None or un is None:
            continue
        diff = abs(ov - un)
        if bm not in bm_main or diff < bm_main[bm][1]:
            bm_main[bm] = (gl, diff)
    gls = [v[0] for v in bm_main.values()]
    if not gls:
        return None, {}
    cnt = Counter(gls)
    return cnt.most_common(1)[0][0], dict(cnt)


async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Prediction).options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= START, Prediction.kickoff_time < END))
        )
        preds = r.unique().scalars().all()

        rows = []
        for p in preds:
            m = p.match
            if not m:
                continue
            r2 = await db.execute(
                select(OddsSnapshot).where(OddsSnapshot.match_id == m.id).order_by(OddsSnapshot.snapshot_time.asc())
            )
            snaps = list(r2.scalars().all())
            lg = m.league.name_zh if m.league else "?"
            if not snaps or p.actual_total_goals is None:
                continue

            times = sorted(set(s.snapshot_time for s in snaps))
            # 开盘共识线: 第一个 over+under 齐全的时刻
            opening_gl = None
            for t in times:
                recs = [(s.goal_line, s.over_odds, s.under_odds, s.bookmaker) for s in snaps if s.snapshot_time == t]
                gl, _ = main_line(recs)
                if gl is not None:
                    opening_gl = gl
                    break
            # 收盘共识线: 开球前最后一个时刻 (去掉开球后快照)
            pre_times = [t for t in times if t < m.kickoff_time]
            last_t = pre_times[-1] if pre_times else None
            closing_gl = None
            if last_t:
                recs = [(s.goal_line, s.over_odds, s.under_odds, s.bookmaker) for s in snaps if s.snapshot_time == last_t]
                closing_gl, _ = main_line(recs)

            if opening_gl is None or closing_gl is None:
                continue
            rows.append({
                "id": m.id, "lg": lg, "match_num": m.match_num, "team": f"{m.home_team_name} vs {m.away_team_name}",
                "kickoff": m.kickoff_time, "open": opening_gl, "close": closing_gl, "act": p.actual_total_goals,
            })

        print(f"严格口径可用场次: {len(rows)}")
        # 排序输出
        rows.sort(key=lambda x: x["kickoff"])

        print(f"\n{'id':<7}{'编号':<8}{'联赛':<7}{'主客':<26}{'开盘':>5}{'收盘':>5}{'Δ':>6}{'实际':>4}{'开|err|':>7}{'收|err|':>7}")
        for x in rows:
            eo = abs(x["open"] - x["act"])
            ec = abs(x["close"] - x["act"])
            print(f"{x['id']:<7}{x['match_num'] or '':<8}{x['lg']:<7}{x['team'][:24]:<26}"
                  f"{x['open']:>5.2f}{x['close']:>5.2f}{x['close']-x['open']:>6.2f}{x['act']:>4}{eo:>7.2f}{ec:>7.2f}")

        # ── 实证一: 线效率 ──
        n = len(rows)
        mae_open = sum(abs(x["open"] - x["act"]) for x in rows) / n
        mae_close = sum(abs(x["close"] - x["act"]) for x in rows) / n
        # 方向命中: 实际>线=大, 实际<线=小 (实际==线 算推盘, 不计)
        def dir_hit(gl, act):
            if act > gl: return 1  # over
            if act < gl: return 0  # under
            return None
        oh = [dir_hit(x["open"], x["act"]) for x in rows]
        ch = [dir_hit(x["close"], x["act"]) for x in rows]
        oh = [h for h in oh if h is not None]
        ch = [h for h in ch if h is not None]
        closer_open = sum(1 for x in rows if abs(x["open"]-x["act"]) < abs(x["close"]-x["act"]))
        closer_close = sum(1 for x in rows if abs(x["close"]-x["act"]) < abs(x["open"]-x["act"]))
        tie = n - closer_open - closer_close

        print("\n" + "=" * 70)
        print("实证一: 开盘线 vs 收盘线(开球前) 效率对比")
        print(f"  MAE  开盘线={mae_open:.3f}  收盘线={mae_close:.3f}")
        print(f"  Over方向命中率: 开盘线={sum(oh)/len(oh)*100:.1f}% ({sum(oh)}/{len(oh)})  收盘线={sum(ch)/len(ch)*100:.1f}% ({sum(ch)}/{len(ch)})")
        print(f"  更接近实际: 开盘线={closer_open} 收盘线={closer_close} 持平={tie}")

        # ── 实证二: 变动方向 vs 结果 ──
        print("\n" + "=" * 70)
        print("实证二: 盘口变动方向 Δ=收盘-开盘 vs 实际进球相对开盘的位置")
        # 变动组
        down = [x for x in rows if x["close"] < x["open"] - 0.01]
        up = [x for x in rows if x["close"] > x["open"] + 0.01]
        flat = [x for x in rows if abs(x["close"] - x["open"]) <= 0.01]
        print(f"  变动分布: 下调={len(down)} 上调={len(up)} 持平={len(flat)}")
        for name, grp in [("下调(收盘<开盘)", down), ("上调(收盘>开盘)", up), ("持平", flat)]:
            if not grp:
                continue
            # 若市场有效: 下调 → 实际应倾向低于开盘 (跟随之); 反转效应则相反
            below_open = sum(1 for x in grp if x["act"] < x["open"])
            above_open = sum(1 for x in grp if x["act"] > x["open"])
            eq_open = len(grp) - below_open - above_open
            # 下调组: 实际高于开盘 = 反转(线下调但进球多)
            rev = above_open if name.startswith("下调") else (below_open if name.startswith("上调") else 0)
            follow = below_open if name.startswith("下调") else (above_open if name.startswith("上调") else 0)
            print(f"  {name}: n={len(grp)} 实际>开盘线={above_open} 实际<开盘线={below_open} 等于={eq_open} "
                  f"→ 反转(线动但结果反向)={rev} 跟随={follow}")
        # 相关性
        deltas = [x["close"] - x["open"] for x in rows]
        actual_minus_open = [x["act"] - x["open"] for x in rows]
        if len(deltas) > 1:
            mx, my = sum(deltas)/len(deltas), sum(actual_minus_open)/len(actual_minus_open)
            cov = sum((d-mx)*(a-my) for d, a in zip(deltas, actual_minus_open)) / (len(deltas)-1)
            sx = (sum((d-mx)**2 for d in deltas) / (len(deltas)-1)) ** 0.5
            sy = (sum((a-my)**2 for a in actual_minus_open) / (len(actual_minus_open)-1)) ** 0.5
            corr = cov / (sx*sy) if sx and sy else 0
            print(f"  相关系数 Δ vs (实际-开盘) = {corr:.3f}   (正=跟随之, 负=反转)")


asyncio.run(main())
