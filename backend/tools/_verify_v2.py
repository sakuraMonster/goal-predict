"""Model C v2 验证（2026-08-11）
1. 同一样本 A/B：OU_OPENING_ANCHOR=True(新) vs False(旧)，严格28场 + 全量
2. 严格28场逐场差异：锚点/信号/λ/top2/命中 对比，定位翻转比赛
3. 15578 稳定性：goal_line_market 恒定为开盘线（多个截止时刻不翻转）

用法: python tools/_verify_v2.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collections import Counter
from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_c import ModelC
from app.predictor.snap import snap_top2
from app import ou_flags

START = datetime(2026, 7, 11, 12, 0, 0)
END = datetime(2026, 8, 11, 12, 0, 0)


def main_line(records):
    bm_main = {}
    for gl, ov, un, bm in records:
        if ov is None or un is None:
            continue
        diff = abs(ov - un)
        if bm not in bm_main or diff < bm_main[bm][1]:
            bm_main[bm] = (gl, diff)
    gls = [v[0] for v in bm_main.values()]
    if not gls:
        return None
    return Counter(gls).most_common(1)[0][0]


async def extract_with(db, mid, anchor_flag):
    """按指定开关状态抽取特征（仅 OU 计算受影响，其余特征一致）"""
    old = ou_flags.OU_OPENING_ANCHOR
    ou_flags.OU_OPENING_ANCHOR = anchor_flag
    try:
        feat = FeatureEngineerB(db)
        fdf = await feat.extract_features(mid)
        return fdf.iloc[0].to_dict() if not fdf.empty else None
    finally:
        ou_flags.OU_OPENING_ANCHOR = old


async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Prediction).options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= START, Prediction.kickoff_time < END))
        )
        preds = r.unique().scalars().all()
        model_c = ModelC()

        # ── 严格口径 28 场（开盘+收盘线均可算） ──
        strict = []
        for p in preds:
            m = p.match
            if not m or not m.league or p.actual_total_goals is None:
                continue
            r2 = await db.execute(
                select(OddsSnapshot).where(OddsSnapshot.match_id == m.id).order_by(OddsSnapshot.snapshot_time.asc())
            )
            snaps = list(r2.scalars().all())
            if not snaps:
                continue
            times = sorted(set(s.snapshot_time for s in snaps))
            opening_gl = None
            for t in times:
                recs = [(s.goal_line, s.over_odds, s.under_odds, s.bookmaker) for s in snaps if s.snapshot_time == t]
                gl = main_line(recs)
                if gl is not None:
                    opening_gl = gl
                    break
            pre_times = [t for t in times if t < m.kickoff_time]
            closing_gl = None
            if pre_times:
                recs = [(s.goal_line, s.over_odds, s.under_odds, s.bookmaker) for s in snaps if s.snapshot_time == pre_times[-1]]
                closing_gl = main_line(recs)
            if opening_gl is None or closing_gl is None:
                continue
            f_off = await extract_with(db, m.id, False)
            f_on = await extract_with(db, m.id, True)
            if not f_off or not f_on:
                continue
            strict.append({
                "id": m.id, "lg": m.league.name_zh, "open": opening_gl, "close": closing_gl,
                "act": p.actual_total_goals, "f_off": f_off, "f_on": f_on,
            })

        print("=" * 96)
        print(f"严格口径 {len(strict)} 场 | 旧(flag off) vs 新(flag on) 同一样本 A/B")
        print(f"{'match':<7}{'联赛':<8}{'open':>6}{'close':>7}{'act':>4}"
              f"{'旧GL':>6}{'旧shift':>8}{'旧λ':>6}{'旧top2':>9}"
              f"{'新GL':>6}{'新shift':>8}{'新λ':>6}{'新top2':>9}  命中")
        hit_off = hit_on = 0
        flip_rows = []
        for it in strict:
            f_off, f_on = it["f_off"], it["f_on"]
            rc_off = model_c.predict(f_off, it["lg"])
            rc_on = model_c.predict(f_on, it["lg"])
            lam_off, top_off = rc_off["expected_goals"], snap_top2(rc_off["expected_goals"])
            lam_on, top_on = rc_on["expected_goals"], snap_top2(rc_on["expected_goals"])
            h_off = it["act"] in top_off
            h_on = it["act"] in top_on
            hit_off += h_off
            hit_on += h_on
            changed = "!!" if top_off != top_on else ("  " if h_off == h_on else "->")
            print(f"{it['id']:<7}{it['lg']:<8}{it['open']:>6.2f}{it['close']:>7.2f}{it['act']:>4}"
                  f"{f_off['goal_line_market']:>6.2f}{f_off['goal_line_shift']:>8.2f}{lam_off:>6.2f}{str(top_off):>9}"
                  f"{f_on['goal_line_market']:>6.2f}{f_on['goal_line_shift']:>8.2f}{lam_on:>6.2f}{str(top_on):>9}"
                  f"  {('旧中' if h_off else '--')}/{('新中' if h_on else '--')} {changed}")
            if top_off != top_on:
                flip_rows.append((it["id"], it["lg"], top_off, top_on, it["act"], f_off["goal_line_market"], f_on["goal_line_market"]))
        print(f"\n严格28场: 旧命中 {hit_off}/{len(strict)} ({hit_off/len(strict)*100:.1f}%)"
              f" | 新命中 {hit_on}/{len(strict)} ({hit_on/len(strict)*100:.1f}%)")
        print(f"top2 翻转场次: {len(flip_rows)}")
        for row in flip_rows:
            print(f"  翻转 {row[0]} {row[1]}: 旧{row[2]} → 新{row[3]} (实际{row[4]}球, 旧GL={row[5]}, 新GL={row[6]})")

        # ── 全量同一样本 A/B ──
        print("\n" + "=" * 96)
        print("全量已结算样本 同一样本 A/B（纯生产行为: 不覆盖 goal_line_market）")
        all_off = all_on = 0
        n_all = 0
        for p in preds:
            m = p.match
            if not m or not m.league or p.actual_total_goals is None:
                continue
            f_off = await extract_with(db, m.id, False)
            f_on = await extract_with(db, m.id, True)
            if not f_off or not f_on:
                continue
            n_all += 1
            top_off = snap_top2(model_c.predict(f_off, m.league.name_zh)["expected_goals"])
            top_on = snap_top2(model_c.predict(f_on, m.league.name_zh)["expected_goals"])
            all_off += p.actual_total_goals in top_off
            all_on += p.actual_total_goals in top_on
        print(f"全量 {n_all} 场: 旧 {all_off} ({all_off/n_all*100:.1f}%) | 新 {all_on} ({all_on/n_all*100:.1f}%)")


asyncio.run(main())
