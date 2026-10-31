"""去抽水方法对比回测（任务1）：TTG 大小球方向命中率 + 分布校准。

背景
----
现状 ou_direction._implied 用朴素 multiplicative（inv/sum）从 TTG 8 档赔率提取
隐含分布，再算 p_big = P(总进球>=3) 做大小球方向门控。本脚本验证：换成
shin / power / logarithmic 去抽水后，p_big 是否更准、方向命中率是否提升、
分布校准（隐含 vs 实际）是否改善。

四路对比（同一场 TTG 赔率，四种去抽水）：
  mult  : multiplicative（= 现状）
  power : power 系数法
  shin  : Shin(1992/1993) 内幕交易者模型
  log   : logarithmic（logit 空间）

评估口径（与 ou_direction 一致）：
  p_big = P(总进球>=3)，门控阈值 full=0.50 / loose=0.58 / standard=0.62 / strict=0.65，
  对称判小阈值 = 1 - 判大阈值。命中 = (判大 and 实际over) or (判小 and 实际under)。

分布质量：RPS（8 档有序）、多分类 Brier、logloss，越低越好。

运行（服务器容器内，DB 仅 docker 内网可达）：
  1) scp implied.py 与本脚本到服务器 /tmp
  2) docker cp 进 ricking-backend-1:/tmp
  3) docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_implied_methods.py
"""
import os
import sys
import json
import asyncio

import numpy as np
import pandas as pd
import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from implied import (  # noqa: E402
    multiplicative_probs,
    power_probs,
    shin_probs,
    logarithmic_probs,
    rps,
    brier_multiclass,
)

# 竞彩 TTG 8 档：0/1/2/3/4/5/6/7+
BUCKET_NAMES = {0: "0球", 1: "1球", 2: "2球", 3: "3球", 4: "4球", 5: "5球", 6: "6球", 7: "7+球"}
N_BUCKETS = 8

METHODS = {
    "mult": multiplicative_probs,
    "power": power_probs,
    "shin": shin_probs,
    "log": logarithmic_probs,
}

# 方向门控档位（对称阈值）
TIERS = {
    "full": 0.50,
    "loose": 0.58,
    "standard": 0.62,
    "strict": 0.65,
}

SQL = """
SELECT
  m.id AS match_id,
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
  AND m.kickoff_time >= '2024-07-15'
ORDER BY m.kickoff_time ASC
"""


def parse_odds(j):
    """TTG 赔率 JSON -> {k: odds}（k=0..7）。无效返回 None。"""
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


def p_big_of(probs):
    return sum(probs.get(k, 0.0) for k in range(3, N_BUCKETS))


def logloss_of(probs, bucket):
    return -np.log(max(probs.get(bucket, 1e-9), 1e-9))


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
    df = df[df["odds"].notna()].copy()

    print("=" * 92)
    print("去抽水方法对比（TTG 大小球方向 + 分布校准）")
    print("=" * 92)
    print(f"已结算场次(含TTG赔率): {len(df)}")
    print(f"kickoff 时间窗: {df['kickoff_time'].min()} ~ {df['kickoff_time'].max()}")

    def _safe(fn):
        def _w(odds):
            try:
                return fn(odds)
            except Exception:
                return None
        return _w

    # 对每场用四种方法算分布
    for name, fn in METHODS.items():
        df[f"probs_{name}"] = df["odds"].apply(_safe(fn))
    df = df[df[[f"probs_{name}" for name in METHODS]].notna().all(axis=1)].copy()
    df = df.reset_index(drop=True)
    for name in METHODS:
        df[f"pbig_{name}"] = df[f"probs_{name}"].apply(p_big_of)

    print(f"四种方法均有效场次: {len(df)}")
    actual_over = (df["bucket"] >= 3).to_numpy()

    # ── [A] p_big 与现状(mult)的差异 ──
    print("\n[A] p_big 相对现状(mult)的偏移")
    print("-" * 92)
    print(f"{'方法':<8s}{'p_big均值':>10s}{'中位差':>10s}{'P25差':>10s}{'P75差':>10s}{'相关(mult)':>12s}")
    base = df["pbig_mult"]
    for name in METHODS:
        x = df[f"pbig_{name}"]
        diff = (x - base)
        corr = np.corrcoef(base, x)[0, 1]
        print(f"{name:<8s}{x.mean()*100:>9.2f}%{np.median(diff)*100:>+9.2f}pp"
              f"{np.percentile(diff,25)*100:>+9.2f}pp{np.percentile(diff,75)*100:>+9.2f}pp{corr:>12.4f}")

    # ── [B] 全局校准：每档 隐含 vs 实际 ──
    print("\n[B] 全局校准：每档 隐含 vs 实际（偏差=隐含-实际，正=高估/诱盘，负=低估/价值）")
    print("-" * 92)
    header = f"{'档位':<8s}{'n':>6s}" + "".join(f"{m:>12s}" for m in METHODS)
    print(header)
    for k in range(N_BUCKETS):
        n = int((df["bucket"] == k).sum())
        line = f"{BUCKET_NAMES[k]:<8s}{n:>6d}"
        for m in METHODS:
            imp = df[f"probs_{m}"].apply(lambda p: p.get(k, 0.0)).mean()
            act = (df["bucket"] == k).mean()
            line += f"{(imp-act)*100:>+11.2f}pp"
        print(line)

    # ── [C] 方向命中率（门控分档） ──
    print("\n[C] 大小球方向命中率（门控分档，覆盖=判大+判小占比）")
    print("-" * 92)
    for tier, thr in TIERS.items():
        print(f"  {tier:<10s}(阈值 {thr:.2f})")
        for name in METHODS:
            pb = df[f"pbig_{name}"].to_numpy()
            over_m = pb >= thr
            under_m = pb <= (1.0 - thr)
            sel = over_m | under_m
            if sel.sum() == 0:
                print(f"    {name:<8s} 无信号")
                continue
            pred_over = pb[sel] >= 0.5
            hit = (pred_over == actual_over[sel]).mean()
            print(f"    {name:<8s} 命中={pct(hit)}  覆盖={pct(sel.mean())}  n={int(sel.sum())}")
        print()

    # ── [D] 分布质量（RPS / Brier / logloss） ──
    print("[D] 8档分布质量（越低越好）")
    print("-" * 92)
    print(f"{'方法':<8s}{'RPS':>12s}{'Brier':>12s}{'logloss':>12s}{'方向Brier':>12s}")
    for name in METHODS:
        probs_col = df[f"probs_{name}"]
        rps_sum = brier_sum = ll_sum = 0.0
        dir_brier_sum = 0.0
        n = len(df)
        for i in df.index:
            p = probs_col.loc[i]
            b = int(df.loc[i, "bucket"])
            # 8 档有序概率数组（补全缺失档）
            arr = np.array([p.get(k, 0.0) for k in range(N_BUCKETS)])
            rps_sum += rps(arr, b)
            brier_sum += brier_multiclass(arr, b)
            ll_sum += logloss_of(p, b)
            pb = p_big_of(p)
            dir_brier_sum += (pb - (1 if b >= 3 else 0)) ** 2
        print(f"{name:<8s}{rps_sum/n:>12.5f}{brier_sum/n:>12.5f}{ll_sum/n:>12.5f}{dir_brier_sum/n:>12.5f}")

    # ── [E] 方向分桶校准（按现状 mult 的 p_big 分桶，看各方法的方向命中） ──
    print("\n[E] 方向分桶校准（按现状 mult 的 p_big 分桶，看各方法 p_big 的命中率）")
    print("-" * 92)
    buckets = [(0.65, 1.01, "≥0.65"), (0.58, 0.65, "0.58-0.65"), (0.52, 0.58, "0.52-0.58"),
               (0.48, 0.52, "0.48-0.52"), (0.42, 0.48, "0.42-0.48"), (0.35, 0.42, "0.35-0.42"),
               (0.0, 0.35, "<0.35")]
    for lo, hi, label in buckets:
        sub = df[(df["pbig_mult"] >= lo) & (df["pbig_mult"] < hi)]
        if len(sub) < 20:
            continue
        line = f"  {label:<10s} n={len(sub):>5d}"
        for name in METHODS:
            pb = sub[f"pbig_{name}"].to_numpy()
            pred_over = pb >= 0.5
            hit = (pred_over == actual_over[sub.index]).mean()
            line += f"  {name}={pct(hit)}"
        print(line)

    print("\n" + "=" * 92)
    print("done")
    print("=" * 92)


if __name__ == "__main__":
    asyncio.run(main())
