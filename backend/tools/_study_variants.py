"""模型变体对比: 同一批比赛, 不同盘口锚定 → SNAP top2 命中率
A: 现状(收盘线+变动信号)   B: 开盘锚定+无变动信号   D: 收盘线+无变动信号(隔离变动机制价值)
E: 开盘锚定+变动信号保留   C: 开盘+0.5Δ(半程跟随)+无信号
样本1: 严格口径28场(有可靠开盘线)  样本2: 全量142场(A vs D, 检验变动机制价值)
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
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_c import ModelC
from app.predictor.snap import snap_top2

START = datetime(2026, 7, 11, 12, 0, 0)
END = datetime(2026, 8, 11, 12, 0, 0)

MOVE_SIGNAL_KEYS = [
    "over_odds_movement", "under_odds_movement", "goal_line_change",
    "goal_line_drop_from_peak", "goal_line_volatility", "over_odds_decline_rate",
    "odds_drift_over_mean", "odds_drift_consensus", "goal_line_shift",
    "goal_line_drop_from_peak_old", "goal_line_max_old", "goal_line_min_old",
    "over_odds_current", "under_odds_current",
]


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


def variant_features(base_f, gl, zero_signals, half_follow=False, opening=None):
    f = dict(base_f)
    if gl is not None:
        f["goal_line_market"] = gl
    if zero_signals:
        for k in MOVE_SIGNAL_KEYS:
            if k in f:
                f[k] = 0.0
        f["goal_line_volatility"] = 0.1
    elif half_follow and opening is not None:
        pass  # Δ 已在 gl 中体现
    return f


async def main():
    async with async_session() as db:
        r = await db.execute(
            select(Prediction).options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= START, Prediction.kickoff_time < END))
        )
        preds = r.unique().scalars().all()
        feat = FeatureEngineerB(db)
        model_c = ModelC()

        # ── 严格口径 28 场: 算开盘/收盘线 ──
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
            fdf = await feat.extract_features(m.id)
            if fdf.empty:
                continue
            strict.append({
                "id": m.id, "lg": m.league.name_zh, "open": opening_gl, "close": closing_gl,
                "act": p.actual_total_goals, "f": fdf.iloc[0].to_dict(),
            })

        print(f"严格口径样本: {len(strict)} 场")

        def run_variant(items, name, gl_fn, zero_signals, half_follow=False):
            hit = 0
            tops = []
            for it in items:
                gl = gl_fn(it)
                f = variant_features(it["f"], gl, zero_signals, half_follow, it.get("open"))
                rc = model_c.predict(f, it["lg"])
                lam = rc["expected_goals"]
                top = snap_top2(lam)
                tops.append((lam, top, it["act"]))
                if it["act"] in top:
                    hit += 1
            n = len(items)
            avg_lam = sum(t[0] for t in tops) / n if n else 0
            return hit, n, avg_lam

        variants = [
            ("A 现状(收盘+信号)", lambda it: it["close"], False),
            ("B 开盘锚定(无信号)", lambda it: it["open"], True),
            ("D 收盘锚定(无信号)", lambda it: it["close"], True),
            ("E 开盘锚定(保留信号)", lambda it: it["open"], False),
            ("C 开盘+0.5Δ(无信号)", lambda it: it["open"] + 0.5 * (it["close"] - it["open"]), True),
        ]
        print(f"\n{'变体':<26}{'命中':>6}{'率':>8}{'平均λ':>8}")
        for name, gl_fn, zero in variants:
            h, n, avgl = run_variant(strict, name, gl_fn, zero)
            print(f"{name:<26}{h:>5}/{n:<3}{h/n*100:>7.1f}%{avgl:>8.2f}")

        # 顺带: 实际进球均值 (基准)
        act_mean = sum(x["act"] for x in strict) / len(strict)
        print(f"\n实际总进球均值 = {act_mean:.2f} (SNAP 若永远猜最频繁值参考)")

        # ── 全量样本 A vs D: 变动信号机制本身的价值 ──
        print("\n" + "=" * 60)
        print("全量已结算样本: 变动信号机制价值 (A 现状 vs D 关闭变动信号)")
        all_items = []
        for p in preds:
            m = p.match
            if not m or not m.league or p.actual_total_goals is None:
                continue
            fdf = await feat.extract_features(m.id)
            if fdf.empty:
                continue
            all_items.append({"id": m.id, "lg": m.league.name_zh, "act": p.actual_total_goals, "f": fdf.iloc[0].to_dict()})
        print(f"全量样本: {len(all_items)} 场")
        for name, gl_fn, zero in [("A 现状(收盘+信号)", lambda it: None, False), ("D 收盘锚定(无信号)", lambda it: None, True)]:
            h, n, avgl = run_variant(all_items, name, gl_fn, zero)
            print(f"{name:<26}{h:>5}/{n:<3}{h/n*100:>7.1f}%{avgl:>8.2f}")


asyncio.run(main())
