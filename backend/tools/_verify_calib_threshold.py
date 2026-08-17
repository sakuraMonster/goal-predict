"""量化验证 方向2：calib 收敛为「仅强信号启用」对命中率的影响（近30天已结算）
隔离变量：同特征，仅切换 calib 配置。
"""
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

from app.db.database import async_session
from app.db.models import Prediction, Match
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

BEIJING_TZ = timezone(timedelta(hours=8))


def hit(tg, snap):
    return tg in list(snap) if snap else False


def apply_calib_threshold(model_c: ModelC, threshold: float):
    """将 calib 收敛为仅强信号：|calib-1|>threshold 保留，否则置 1.0（含 default）"""
    for ln, p in model_c.LEAGUE_PARAMS.items():
        calib = p.get("calib", 0.95)
        if abs(calib - 1.0) <= threshold:
            p["calib"] = 1.0
        # 强信号保留原值


async def main():
    now = datetime.now(BEIJING_TZ).replace(tzinfo=None)
    since = now - timedelta(days=30)
    model_c = ModelC()

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= since, Prediction.kickoff_time < now))
            .order_by(Prediction.kickoff_time)
        )
        preds = list(result.unique().scalars().all())
        settled = [p for p in preds if p.actual_home_score is not None]

        # 保存原始 params（深拷贝 calib）
        saved = {ln: dict(p) for ln, p in ModelC.LEAGUE_PARAMS.items()}

        thresholds = [0.15, 0.20, 0.10]

        for th in thresholds:
            # 重置为原始
            ModelC.LEAGUE_PARAMS = {ln: dict(p) for ln, p in saved.items()}
            # 应用方向2阈值
            apply_calib_threshold(model_c, th)

            agg = defaultdict(lambda: {"n": 0, "cur": 0, "v2": 0, "cur_err": 0.0, "v2_err": 0.0})
            for p in settled:
                tg = (p.actual_home_score or 0) + (p.actual_away_score or 0)
                ln = p.match.league.name_zh if (p.match and p.match.league) else "未知"
                try:
                    features_df = await feat_engine.extract_features(p.match_id)
                    if features_df.empty:
                        continue
                    features = features_df.iloc[0].to_dict()

                    # 方向2 的 λc（当前 LEAGUE_PARAMS 已收敛）
                    lc_v2 = model_c.predict(features, ln)["expected_goals"]

                    # 当前生产 calib 的 λc（临时还原该联赛原始 calib）
                    orig_calib = saved.get(ln, saved["default"])["calib"]
                    tmp = ModelC.LEAGUE_PARAMS[ln]["calib"]
                    ModelC.LEAGUE_PARAMS[ln]["calib"] = orig_calib
                    lc_cur = model_c.predict(features, ln)["expected_goals"]
                    ModelC.LEAGUE_PARAMS[ln]["calib"] = tmp

                    a = agg[ln]
                    a["n"] += 1
                    a["cur"] += hit(tg, snap_top2(lc_cur))
                    a["v2"] += hit(tg, snap_top2(lc_v2))
                    a["cur_err"] += abs(lc_cur - tg)
                    a["v2_err"] += abs(lc_v2 - tg)
                except Exception:
                    pass

            # 汇总
            t = {"n": 0, "cur": 0, "v2": 0, "cur_err": 0.0, "v2_err": 0.0}
            for a in agg.values():
                for k in ("n", "cur", "v2", "cur_err", "v2_err"):
                    t[k] += a[k]

            n = t["n"]
            cur_h = t["cur"] / n * 100
            v2_h = t["v2"] / n * 100
            print("=" * 90)
            print(f"方向2 阈值 |ratio-1|>{th}  （整体 n={n}）")
            print(f"  当前生产 calib: 命中 {cur_h:.1f}%  MAE {t['cur_err']/n:.2f}")
            print(f"  方向2收敛calib: 命中 {v2_h:.1f}%  MAE {t['v2_err']/n:.2f}")
            print(f"  → 命中率变化 {v2_h-cur_h:+.1f}pp，MAE变化 {t['v2_err']/n-t['cur_err']/n:+.2f}")
            print("-" * 90)
            print(f"{'联赛':<8}{'n':>4}  {'当前':>7}  {'方向2':>7}  {'变化':>7}")
            rows = []
            for ln, a in agg.items():
                if a["n"] < 4:
                    continue
                cur = a["cur"] / a["n"] * 100
                v2 = a["v2"] / a["n"] * 100
                rows.append((ln, a["n"], cur, v2, v2 - cur))
            rows.sort(key=lambda x: x[4])
            for ln, nn, cur, v2, d in rows:
                print(f"{ln:<8}{nn:>4}  {cur:>6.1f}%  {v2:>6.1f}%  {d:>+6.1f}")

        # 恢复原始
        ModelC.LEAGUE_PARAMS = {ln: dict(p) for ln, p in saved.items()}


if __name__ == "__main__":
    asyncio.run(main())
