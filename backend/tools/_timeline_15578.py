"""周一001(15578) 时间线重建: 对每个快照截止时刻重算 OU 特征 + λ_c
验证"中午[3,4] → 晚上[2,3]"是由盘口特征随时间漂移导致
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import OddsSnapshot
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_c import ModelC
from app.predictor.snap import snap_top2

MID = 15578
LEAGUE = "瑞典超"

# 需要重算的关键截止时刻（北京时间）
CUTOFFS = [
    datetime(2026, 8, 9, 0, 52),
    datetime(2026, 8, 9, 5, 9),
    datetime(2026, 8, 9, 12, 0),
    datetime(2026, 8, 9, 20, 0),
    datetime(2026, 8, 10, 0, 0),
    datetime(2026, 8, 10, 6, 0),
    datetime(2026, 8, 10, 12, 0),
    datetime(2026, 8, 10, 15, 0),
    datetime(2026, 8, 10, 18, 0),
    datetime(2026, 8, 10, 20, 0),
    datetime(2026, 8, 10, 22, 0),
    datetime(2026, 8, 10, 23, 59),
    datetime(2026, 8, 11, 1, 0),
    datetime(2026, 8, 11, 7, 0),
]

OU_FIELDS = [
    "goal_line_market", "over_odds_current", "under_odds_current",
    "over_odds_movement", "under_odds_movement", "goal_line_change",
    "goal_line_drop_from_peak", "goal_line_volatility", "over_odds_decline_rate",
    "odds_drift_over_mean", "odds_drift_consensus", "goal_line_shift",
    "goal_line_market_old", "goal_line_drop_from_peak_old",
    "goal_line_max_old", "goal_line_min_old",
]


async def main():
    async with async_session() as db:
        r = await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id == MID).order_by(OddsSnapshot.snapshot_time.asc())
        )
        all_odds = list(r.scalars().all())
        print(f"总快照 {len(all_odds)} 条")

        feat = FeatureEngineerB(db)
        model_c = ModelC()

        # 取全量特征（球队/联赛等非盘口部分），仅覆盖盘口字段
        fdf = await feat.extract_features(MID)
        f = fdf.iloc[0].to_dict()

        # 真实当前(全量)复现
        rc_full = model_c.predict(f, LEAGUE)
        print(f"\n当前全量特征: λ_c={rc_full['expected_goals']} top2={snap_top2(rc_full['expected_goals'])} "
              f"GL={f['goal_line_market']}")

        print(f"\n{'截止时刻':<18}{'best_gl':>8}{'shift':>7}{'drift':>8}{'vol':>6}{'λc':>7}{'top2':>10}")
        for cutoff in CUTOFFS:
            subset = [o for o in all_odds if o.snapshot_time <= cutoff]
            if not subset:
                print(f"{cutoff:%m-%d %H:%M:<16} 无快照")
                continue
            by_bm = {}
            for o in subset:
                bm = o.bookmaker or "unknown"
                by_bm.setdefault(bm, []).append(o)
            times = sorted(set(o.snapshot_time for o in subset))
            latest = [o for o in subset if o.snapshot_time == times[-1]]

            ou = feat._compute_ou_features(subset, times, by_bm, latest, LEAGUE)
            f2 = dict(f)
            for k in OU_FIELDS:
                if k in ou:
                    f2[k] = ou[k]
            rc = model_c.predict(f2, LEAGUE)
            d = rc["detail"]
            print(f"{cutoff:%m-%d %H:%M:<16}{ou['goal_line_market']:>8.2f}"
                  f"{ou['goal_line_shift']:>7.2f}{ou['odds_drift_over_mean']:>8.3f}"
                  f"{ou['goal_line_volatility']:>6.2f}{rc['expected_goals']:>7.2f}"
                  f"{str(snap_top2(rc['expected_goals'])):>10}"
                  f"  mw={d['market_weight']:.3f} λmkt={d['lambda_market']:.2f}")


asyncio.run(main())
