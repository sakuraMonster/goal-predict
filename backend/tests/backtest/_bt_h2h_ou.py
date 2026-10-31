"""H2H 交锋记录对 MarketFlow V2 大小球判定的增量价值回测 v2（离线一次性脚本）。

问题：现有 OU 判定纯市场口径（TTG 隐含 / SM O/U 去抽水，2.5 线），
standard 门控命中 ~70%、覆盖 ~30%。用户设想：用两队历史交锋的进球数分布
对 gated 结果做「精度过滤」（同向保留、反向降级），并要求列出降级理由。

v2 在 v1 基础上补充：
  - agree vs disagree 的两比例 z 检验（判断差异是否显著）
  - "强反向" 门限变体（over_rate<=.33 / avg_total<=2.0 / btts<=.33 等）的降级模拟
  - 降级理由分类明细（按 市场方向×H2H方向×强度 分组的原命中率）

口径与生产一致：以 pool_freeze_json 的冻结值为准；防泄露用 kickoff-8h 之前的交锋。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt.py
"""
import os
import asyncio
from math import sqrt

import numpy as np
import pandas as pd
import asyncpg

SQL = """
SELECT
  mfp.match_id,
  mfp.matchday_date::text AS matchday,
  (m.home_score + m.away_score) AS actual_tg,
  (mfp.pool_freeze_json->'ou_sm'->>'p_big')::float AS sm_p_big,
  (mfp.pool_freeze_json->'ou_sm'->'tiers'->>'standard') AS sm_std,
  (mfp.pool_freeze_json->'ou_sm'->'tiers'->>'strict') AS sm_strict,
  (mfp.pool_freeze_json->'ou_all'->'standard'->>'p_big')::float AS ttg_p_big,
  (mfp.pool_freeze_json->'ou_all'->'standard'->>'direction') AS ttg_std,
  (mfp.pool_freeze_json->'ou_all'->'strict'->>'direction') AS ttg_strict,
  h.n AS h2h_n, h.over_rate, h.avg_total, h.btts_rate
FROM market_flow_predictions mfp
JOIN matches m ON m.id = mfp.match_id
LEFT JOIN LATERAL (
  SELECT count(*) AS n,
         avg((x.home_score + x.away_score > 2.5)::int)::float AS over_rate,
         avg((x.home_score + x.away_score)::float)::float AS avg_total,
         avg((x.home_score > 0 AND x.away_score > 0)::int)::float AS btts_rate
  FROM (
    SELECT hh.home_score, hh.away_score
    FROM head_to_head hh
    WHERE ((hh.home_team_id = m.home_team_id AND hh.away_team_id = m.away_team_id)
        OR (hh.home_team_id = m.away_team_id AND hh.away_team_id = m.home_team_id))
      AND hh.home_score IS NOT NULL AND hh.away_score IS NOT NULL
      AND hh.match_date::date < (m.kickoff_time - interval '8 hours')::date
    ORDER BY hh.match_date DESC
    LIMIT 10
  ) x
) h ON true
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND mfp.pool_freeze_json IS NOT NULL
"""

LINE = 2.5
PRIOR_OVER, PRIOR_AVG, PRIOR_BTTS = 0.52, 2.65, 0.50
K = 3.0

# (显示名, 收缩列, 弱阈值, 强阈值)  判大 = stat >= 阈值
_STATS = [
    ("over_rate", "ov_s", 0.50, 0.67),
    ("avg_total", "av_s", 2.50, 3.00),
    ("btts_rate", "bt_s", 0.50, 0.67),
]


def _pct(x):
    return "  n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:5.1f}%"


def _shrunk(vals, n, prior):
    m = n > 0
    out = np.full(len(n), np.nan)
    out[m] = (vals[m] * n[m] + prior * K) / (n[m] + K)
    return out


def _z(h1, n1, h2, n2):
    if n1 == 0 or n2 == 0:
        return float("nan")
    p = (h1 + h2) / (n1 + n2)
    se = sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (h1 / n1 - h2 / n2) / se if se > 0 else float("nan")


def _bucket_table(df, col_raw, edges, label):
    print(f"\n  [monotonicity] {label} -> actual over-rate (all settled, n>=1)")
    sub = df[df["h2h_n"] >= 1]
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else None
        m = sub[col_raw] >= lo
        if hi is not None:
            m &= sub[col_raw] < hi
        seg = sub[m]
        rng = f"[{lo:.2f},{hi:.2f})" if hi is not None else f"[{lo:.2f},+]"
        if len(seg) == 0:
            print(f"    {rng:>14}  n=0")
            continue
        print(f"    {rng:>14}  n={len(seg):4d}  actual_over={_pct(seg['actual_over'].mean())}")


def gate_analysis(df, gate_col, label):
    g = df[df[gate_col].isin(["over", "under"])].copy()
    if g.empty:
        print(f"\n=== {label}: no gated rows ===")
        return
    g["hit"] = np.where(g["actual_over"], "over", "under") == g[gate_col]
    base_hit = g["hit"].mean()
    print(f"\n=== {label}  n={len(g)}  baseline_hit={_pct(base_hit)} ===")

    for name, scol, thr_w, thr_s in _STATS:
        hd_w = np.where(g[scol].isna(), None, np.where(g[scol] >= thr_w, "over", "under"))
        hd_s = np.where(g[scol].isna(), None,
                        np.where(g[scol] >= thr_s, "over",
                                 np.where(g[scol] <= (2 * thr_w - thr_s), "under", None)))
        g["_w"] = hd_w
        g["_s"] = hd_s
        gg = g[g["_w"].notna()].copy()
        if gg.empty:
            print(f"  {name}: no coverage")
            continue
        agree = gg[gg["_w"] == gg[gate_col]]
        dis = gg[gg["_w"] != gg[gate_col]]
        n1, h1 = len(agree), int(agree["hit"].sum())
        n2, h2 = len(dis), int(dis["hit"].sum())
        z = _z(h1, n1, h2, n2)
        a_hit = h1 / n1 if n1 else float("nan")
        d_hit = h2 / n2 if n2 else float("nan")
        print(
            f"  {name:10s} [weak thr] H2H avail={len(gg)/len(g)*100:5.1f}% of gated | "
            f"agree n={n1:4d} hit={_pct(a_hit)} | disagree n={n2:4d} hit={_pct(d_hit)} | "
            f"gap={'' if np.isnan(a_hit) or np.isnan(d_hit) else f'{(a_hit-d_hit)*100:+5.1f}pp'} z={z:+.2f}"
        )
        # 强反向降级模拟：仅当 H2H 强反向时才剔除
        sd = gg[(gg["_s"].notna()) & (gg["_s"].astype(str) != gg[gate_col].astype(str))]
        kept = gg.drop(index=sd.index) if len(sd) else gg
        k_hit = kept["hit"].mean() if len(kept) else float("nan")
        print(
            f"      [strong-downgrade] drop n={len(sd):3d} "
            f"(their orig_hit={_pct(sd['hit'].mean() if len(sd) else float('nan'))}) -> "
            f"kept hit={_pct(k_hit)} cover={len(kept)/len(g)*100:4.1f}% of gated "
            f"(vs baseline {_pct(base_hit)})"
        )
        # 降级理由分类明细
        if len(dis):
            for d_dir in ("over", "under"):
                grp = dis[dis[gate_col] == d_dir]
                if grp.empty:
                    continue
                print(
                    f"      reason: H2H `{name}` says opposite-of-`{d_dir}` n={len(grp):3d} "
                    f"orig_hit={_pct(grp['hit'].mean())} "
                    f"(market said {d_dir}, still hit this often)"
                )

    # 残差检验
    print("  [residual] corr(actual_over - p_big, H2H stat) on gated set:")
    for name, scol, _a, _b in _STATS:
        sub = g[[scol]].copy()
        pcol = "sm_p_big" if label.startswith("SM") else "ttg_p_big"
        sub["_r"] = g["actual_over"].astype(int) - g[pcol]
        sub = sub.dropna()
        r = sub[scol].corr(sub["_r"]) if len(sub) > 5 else float("nan")
        print(f"    {name:10s} r={r:+.4f}  (n={len(sub)})")


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()
    df = pd.DataFrame([dict(r) for r in rows])

    df["actual_tg"] = pd.to_numeric(df["actual_tg"], errors="coerce")
    df = df[df["actual_tg"].notna()].copy()
    df["actual_over"] = df["actual_tg"] > LINE
    df["h2h_n"] = pd.to_numeric(df["h2h_n"], errors="coerce").fillna(0).astype(int)
    for c in ["sm_std", "sm_strict", "ttg_std", "ttg_strict"]:
        df[c] = df[c].fillna("skip")
    for c in ["sm_p_big", "ttg_p_big", "over_rate", "avg_total", "btts_rate"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    n = df["h2h_n"].to_numpy()
    df["ov_s"] = _shrunk(df["over_rate"].fillna(0).to_numpy(), n, PRIOR_OVER)
    df["av_s"] = _shrunk(df["avg_total"].fillna(0).to_numpy(), n, PRIOR_AVG)
    df["bt_s"] = _shrunk(df["btts_rate"].fillna(0).to_numpy(), n, PRIOR_BTTS)

    print("=" * 82)
    print("H2H -> OU precision-filter backtest v2  (settled MarketFlow V2)")
    print("=" * 82)
    print(f"settled rows          : {len(df)}")
    print(f"matchday range        : {df['matchday'].min()} ~ {df['matchday'].max()}")
    print(f"base actual_over rate : {_pct(df['actual_over'].mean())}")
    print(f"H2H coverage (n>=1)   : {(df['h2h_n'] >= 1).mean()*100:.1f}%")
    print("h2h_n distribution    : " + ", ".join(
        f"{k}:{v}" for k, v in df["h2h_n"].value_counts().sort_index().items()))
    sub = df[df["h2h_n"] >= 1]
    print(f"  mean over_rate={sub['over_rate'].mean():.3f}  avg_total={sub['avg_total'].mean():.3f}"
          f"  btts={sub['btts_rate'].mean():.3f}")

    _bucket_table(df, "over_rate", [0.0, 0.25, 0.5, 0.75], "over_rate")
    _bucket_table(df, "avg_total", [0.0, 2.0, 2.5, 3.0], "avg_total")
    _bucket_table(df, "btts_rate", [0.0, 0.34, 0.67, 1.01], "btts_rate")

    gate_analysis(df, "sm_std", "SM O/U standard")
    gate_analysis(df, "sm_strict", "SM O/U strict")
    gate_analysis(df, "ttg_std", "TTG standard")
    gate_analysis(df, "ttg_strict", "TTG strict")

    print("\n" + "=" * 82)
    print("done")
    print("=" * 82)


if __name__ == "__main__":
    asyncio.run(main())
