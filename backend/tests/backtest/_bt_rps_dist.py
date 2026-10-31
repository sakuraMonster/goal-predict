"""分布质量评估回测（任务2）：用 RPS / Brier / logloss 对比 market 与 Dixon-Coles。

背景
----
现状评估进球数模型主要看「方向命中 / Top3 覆盖」等二值口径。本脚本引入 RPS
（Ranked Probability Score，有序分类的严格评分）与多分类 Brier，从「分布质量」
维度对比三路：
  market-mult : 竞彩 TTG 隐含（朴素 multiplicative 去抽水，= 现状）
  market-shin : 竞彩 TTG 隐含（Shin 去抽水）
  dc          : 纯基本面 Dixon-Coles（walk-forward 逐周重训，防泄露）

三路统一到竞彩 8 档（0/1/2/3/4/5/6/7+）口径，RPS/Brier/logloss 越低越好。

运行（服务器容器内）：
  1) scp implied.py、dixon_coles.py 与本脚本到服务器 /tmp
  2) docker cp 进 ricking-backend-1:/tmp
  3) docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_rps_dist.py
"""
import os
import sys
import json
import asyncio
from datetime import timedelta

import numpy as np
import pandas as pd
import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dixon_coles import DixonColes  # noqa: E402
from implied import shin_probs, multiplicative_probs, rps, brier_multiclass  # noqa: E402

REFIT_DAYS = 7
WARMUP_DAYS = 180
WARMUP_MIN_MATCHES = 3000
XI = 0.003
L2_REG = 0.5

BUCKET_NAMES = {0: "0球", 1: "1球", 2: "2球", 3: "3球", 4: "4球", 5: "5球", 6: "6球", 7: "7+球"}
N_BUCKETS = 8

SQL = """
SELECT
  m.id AS match_id,
  m.home_team_id, m.away_team_id,
  m.home_score, m.away_score,
  m.kickoff_time,
  o.ttg_odds_json
FROM matches m
LEFT JOIN LATERAL (
  SELECT js.ttg_odds_json FROM jczq_play_odds_snapshots js
  WHERE js.match_id = m.id AND js.ttg_odds_json IS NOT NULL
  ORDER BY js.snapshot_time DESC LIMIT 1
) o ON true
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
  AND m.kickoff_time >= '2024-07-15'
ORDER BY m.kickoff_time ASC
"""


def parse_odds(j):
    if j is None:
        return None
    if isinstance(j, str):
        try:
            j = json.loads(j)
        except (ValueError, TypeError):
            return None
    if not isinstance(j, dict):
        return None
    odds = {}
    for k, v in j.items():
        try:
            kk = int(k)
            oo = float(v)
        except (ValueError, TypeError):
            continue
        if oo > 1.0:
            odds[kk] = oo
    return odds or None


def to_array(probs):
    """{k: p} -> 8 档有序数组（补全缺失档）。"""
    return np.array([probs.get(k, 0.0) for k in range(N_BUCKETS)], dtype=np.float64)


def cap_probs(total_dist):
    """Dixon-Coles 0..19 全分布 -> 8 档。"""
    p = {k: 0.0 for k in range(N_BUCKETS)}
    for k, v in total_dist.items():
        p[min(int(k), 7)] += float(v)
    s = sum(p.values())
    return {k: v / s for k, v in p.items()} if s > 0 else p


def p_big(probs):
    return sum(probs.get(k, 0.0) for k in range(3, N_BUCKETS))


def top3(probs):
    return sorted(probs, key=lambda k: probs.get(k, 0.0), reverse=True)[:3]


def pct(x):
    return f"{x*100:6.2f}%"


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()

    df = pd.DataFrame([dict(r) for r in rows])
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"]).copy()
    df["total"] = (df["home_score"] + df["away_score"]).astype(int)
    df["bucket"] = df["total"].clip(upper=7)
    df["odds"] = df["ttg_odds_json"].apply(parse_odds)

    def _safe(fn):
        def _w(o):
            if not o:
                return None
            try:
                return fn(o)
            except Exception:
                return None
        return _w

    df["mkt_mult"] = df["odds"].apply(_safe(multiplicative_probs))
    df["mkt_shin"] = df["odds"].apply(_safe(shin_probs))

    print("=" * 92)
    print("分布质量评估（RPS / Brier / logloss）：market vs Dixon-Coles")
    print("=" * 92)
    print(f"已结算场次(含team+TTG): {len(df)}")
    print(f"kickoff 时间窗: {df['kickoff_time'].min()} ~ {df['kickoff_time'].max()}")

    min_dt = df["kickoff_time"].min()
    max_dt = df["kickoff_time"].max()
    refit_date = min_dt + timedelta(days=(WARMUP_DAYS // REFIT_DAYS) * REFIT_DAYS)

    results = []
    refit_count = 0
    dc = None
    cur = refit_date
    while cur < max_dt:
        nxt = cur + timedelta(days=REFIT_DAYS)
        train = df[df["kickoff_time"] < cur]
        test = df[(df["kickoff_time"] >= cur) & (df["kickoff_time"] < nxt)]

        if len(train) >= WARMUP_MIN_MATCHES and train["home_team_id"].nunique() >= 20:
            try:
                dc = DixonColes(xi=XI, l2_reg=L2_REG)
                dc.fit(
                    train["home_team_id"].tolist(),
                    train["away_team_id"].tolist(),
                    train["home_score"].astype(int).tolist(),
                    train["away_score"].astype(int).tolist(),
                    train["kickoff_time"].tolist(),
                    ref_date=cur,
                )
                refit_count += 1
            except Exception as e:  # noqa
                print(f"  [warn] refit @ {cur.date()} 失败: {e}，沿用上一轮")

        if dc is not None and len(test):
            for _, r in test.iterrows():
                if r["mkt_mult"] is None or r["mkt_shin"] is None:
                    continue
                try:
                    dc_out = dc.predict(r["home_team_id"], r["away_team_id"])
                    dc_probs = cap_probs(dc_out["total_dist"])
                except Exception:
                    continue
                odds_map = json.loads(r["ttg_odds_json"]) if isinstance(r["ttg_odds_json"], str) else r["ttg_odds_json"]
                results.append({
                    "mkt_mult": r["mkt_mult"],
                    "mkt_shin": r["mkt_shin"],
                    "dc": dc_probs,
                    "bucket": int(r["bucket"]),
                    "odds": {int(k): float(v) for k, v in odds_map.items()} if isinstance(odds_map, dict) else {},
                })
        cur = nxt

    print(f"\n重训轮数: {refit_count}  |  评估场次: {len(results)}")
    if not results:
        print("无评估样本，终止")
        return

    R = pd.DataFrame(results)

    def report(label, col):
        rps_s = brier_s = ll_s = 0.0
        dir_hit = t3_hit = 0
        payout = 0.0
        n = len(R)
        for i in R.index:
            p = R.loc[i, col]
            b = int(R.loc[i, "bucket"])
            arr = to_array(p)
            rps_s += rps(arr, b)
            brier_s += brier_multiclass(arr, b)
            ll_s += -np.log(max(p.get(b, 1e-9), 1e-9))
            pb = p_big(p)
            dir_hit += int((pb >= 0.5) == (b >= 3))
            sel = top3(p)
            if b in sel:
                t3_hit += 1
                payout += R.loc[i, "odds"].get(b, 0.0)
        roi = payout / (3 * n) if n else float("nan")
        print(f"  {label:<12s} n={n:5d}  RPS={rps_s/n:6.4f}  Brier={brier_s/n:6.4f}  "
              f"logloss={ll_s/n:6.4f}  方向={pct(dir_hit/n)}  Top3={pct(t3_hit/n)}  ROI(3选)={roi:6.3f}")

    print("\n[A] 三路分布质量 + 下注指标对比")
    print("-" * 92)
    report("market-mult", "mkt_mult")
    report("market-shin", "mkt_shin")
    report("dc", "dc")

    # 方向门控命中率
    print("\n[B] 大小球方向门控命中率（|P_big - 0.5| 阈值）")
    print("-" * 92)
    actual_over = (R["bucket"] >= 3).to_numpy()
    for name, col in [("market-mult", "mkt_mult"), ("market-shin", "mkt_shin"), ("dc", "dc")]:
        po = R[col].apply(p_big).to_numpy()
        line = f"  {name:<12s}"
        for thr in [0.0, 0.08, 0.12, 0.18, 0.25]:
            mask = np.abs(po - 0.5) >= thr
            if mask.sum() == 0:
                line += "  n/a"
                continue
            hit = ((po[mask] >= 0.5) == actual_over[mask]).mean()
            line += f"  |Δ|>={thr}: {pct(hit)}(n={int(mask.sum())})"
        print(line)

    print("\n" + "=" * 92)
    print("done")
    print("=" * 92)


if __name__ == "__main__":
    asyncio.run(main())
