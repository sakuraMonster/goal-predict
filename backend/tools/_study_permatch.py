"""逐场分解: A(现状) vs D(关闭信号) vs E(开盘锚定+信号) 的 SNAP 差异
看变动信号机制帮助/伤害的模式
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


def variant_features(base_f, gl, zero_signals):
    f = dict(base_f)
    if gl is not None:
        f["goal_line_market"] = gl
    if zero_signals:
        for k in MOVE_SIGNAL_KEYS:
            if k in f:
                f[k] = 0.0
        f["goal_line_volatility"] = 0.1
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

        rows = []
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
            rows.append({
                "id": m.id, "lg": m.league.name_zh, "num": m.match_num or "", "team": f"{m.home_team_name} vs {m.away_team_name}",
                "open": opening_gl, "close": closing_gl, "act": p.actual_total_goals, "f": fdf.iloc[0].to_dict(),
            })

        # 变体
        def run(fn):
            out = []
            for it in rows:
                f = variant_features(it["f"], fn(it), False) if fn else it["f"]
                rc = model_c.predict(f, it["lg"])
                out.append((rc["expected_goals"], snap_top2(rc["expected_goals"])))
            return out

        a = run(lambda it: None)          # 现状: 特征原样(收盘锚+信号)
        d = run(lambda it: None)          # 占位, 下方单独处理 zero
        d = []
        for it in rows:
            f = variant_features(it["f"], None, True)
            rc = model_c.predict(f, it["lg"])
            d.append((rc["expected_goals"], snap_top2(rc["expected_goals"])))
        e = run(lambda it: it["open"])

        print(f"{'id':<7}{'编号':<8}{'联赛':<6}{'主客':<26}{'开盘':>4}{'收盘':>4}{'Δ':>5}{'实际':>3}  "
              f"{'A_λ':>5}{'A_top':>7}{'D_top':>7}{'E_top':>7}  备注")
        for i, it in enumerate(rows):
            note = ""
            ah = it["act"] in a[i][1]
            dh = it["act"] in d[i][1]
            eh = it["act"] in e[i][1]
            if ah and not dh:
                note = "信号机制救回"
            elif not ah and dh:
                note = "信号机制伤害"
            elif not ah and eh:
                note = "开盘锚定救回"
            print(f"{it['id']:<7}{it['num']:<8}{it['lg']:<6}{it['team'][:24]:<26}"
                  f"{it['open']:>4.2f}{it['close']:>4.2f}{it['close']-it['open']:>5.2f}{it['act']:>3}  "
                  f"{a[i][0]:>5.2f}{str(a[i][1]):>7}{str(d[i][1]):>7}{str(e[i][1]):>7}  {note}")

        # 汇总
        hA = sum(1 for i, it in enumerate(rows) if it["act"] in a[i][1])
        hD = sum(1 for i, it in enumerate(rows) if it["act"] in d[i][1])
        hE = sum(1 for i, it in enumerate(rows) if it["act"] in e[i][1])
        n = len(rows)
        print(f"\nA={hA}/{n} ({hA/n*100:.1f}%)  D={hD}/{n} ({hD/n*100:.1f}%)  E={hE}/{n} ({hE/n*100:.1f}%)")


asyncio.run(main())
