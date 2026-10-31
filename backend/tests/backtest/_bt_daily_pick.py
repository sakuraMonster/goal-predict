"""「每日单场优选」策略回测 —— 基线 vs 天花板。

策略：按天为周期，每天只挑「1/2/3/4 球中双选（选 2 档）最可能命中」的一场，
      命中即当日命中。目标是每日命中率（稳定性），不强求全对。

本脚本建立地基：
  [A] 基线：每天选「市场隐含双选概率最高」的一场，当日命中率。
  [B] 天花板(oracle)：当天只要有一场双选命中即算命中（已知结果的上帝视角上限）。
  [C] 关键分布：每天场次数、最高双选概率分布、双选组合分布。

双选口径：从 {1,2,3,4} 里取市场隐含概率最高的 2 档（动态）。实际进球 ∈ 这 2 档 = 命中。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_daily_pick.py
"""
import os, sys, json, asyncio
from datetime import timedelta

import numpy as np
import pandas as pd
import asyncpg

SQL = """
SELECT
  m.id AS match_id,
  m.home_score, m.away_score,
  m.kickoff_time,
  l.name_zh AS league_name,
  o.ttg_odds_json
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN LATERAL (
  SELECT js.ttg_odds_json FROM jczq_play_odds_snapshots js
  WHERE js.match_id = m.id AND js.ttg_odds_json IS NOT NULL
  ORDER BY js.snapshot_time DESC LIMIT 1
) o ON true
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND m.kickoff_time IS NOT NULL
ORDER BY m.kickoff_time ASC
"""


def ttg_to_probs(j):
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
            kk = int(k); oo = float(v)
        except (ValueError, TypeError):
            continue
        if oo > 1.0:
            odds[kk] = oo
    if not odds:
        return None
    inv = {k: 1.0 / o for k, o in odds.items()}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s > 0 else None


def double_pick(probs):
    """从 {1,2,3,4} 里取隐含最高的 2 档。返回 (选中的2档, 双选概率)。"""
    sub = {k: probs.get(k, 0.0) for k in [1, 2, 3, 4]}
    top2 = sorted(sub, key=lambda k: sub[k], reverse=True)[:2]
    return top2, sum(sub[k] for k in top2)


def _pct(x):
    return f"{x*100:5.1f}%"


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
    df["probs"] = df["ttg_odds_json"].apply(ttg_to_probs)
    df = df[df["probs"].notna()].copy()

    picks = df["probs"].apply(double_pick)
    df["pick2"] = picks.apply(lambda x: x[0])
    df["pick_prob"] = picks.apply(lambda x: x[1])
    df["hit"] = df.apply(lambda r: int(r["bucket"] in r["pick2"]), axis=1)

    # 竞彩比赛日 = kickoff - 12h 取 date
    df["day"] = (df["kickoff_time"] - timedelta(hours=12)).dt.date

    print("=" * 88)
    print("「每日单场优选」策略：基线 vs 天花板")
    print("=" * 88)
    print(f"已结算场次(含TTG): {len(df)}")
    print(f"时间窗: {df['kickoff_time'].min()} ~ {df['kickoff_time'].max()}")
    print(f"比赛日数: {df['day'].nunique()}")

    # [C] 分布
    print("\n[C] 关键分布")
    print("-" * 88)
    per_day = df.groupby("day").size()
    print(f"  每天场次数: 中位={per_day.median():.0f}  均值={per_day.mean():.1f}  min={per_day.min()}  max={per_day.max()}")
    print(f"  单场双选命中率(全量): {_pct(df['hit'].mean())}")
    print(f"  双选组合分布: " + ", ".join(
        f"{sorted(p)}:{int((df['pick2'].apply(lambda x: tuple(sorted(x)))==tuple(sorted(p))).sum())}"
        for p in [(1,2),(2,3),(3,4),(1,3),(2,4)]))
    print(f"  市场最高双选概率(每日max)分布: "
          f"均值={df.groupby('day')['pick_prob'].max().mean()*100:.1f}%  "
          f"中位={df.groupby('day')['pick_prob'].max().median()*100:.1f}%")

    # [A] 基线
    print("\n[A] 基线：每天选「市场双选概率最高」的一场")
    print("-" * 88)
    # 按天取 pick_prob 最高的场次
    idx = df.groupby("day")["pick_prob"].idxmax()
    top = df.loc[idx]
    base_hit = top["hit"].mean()
    print(f"  参与比赛日: {len(top)}")
    print(f"  每日命中率(基线) = {_pct(base_hit)}   (即 {int(top['hit'].sum())}/{len(top)} 天命中)")

    # [B] 天花板
    print("\n[B] 天花板(oracle)：当天只要有任意一场双选命中即算命中")
    print("-" * 88)
    day_hit = df.groupby("day")["hit"].max()  # 1 = 该天至少一场命中
    oracle_hit = day_hit.mean()
    print(f"  参与比赛日: {len(day_hit)}")
    print(f"  每日命中率(天花板) = {_pct(oracle_hit)}")

    # 差距 = 优化空间
    print(f"\n  优化空间 = 天花板 {_pct(oracle_hit)} - 基线 {_pct(base_hit)} = "
          f"{_pct(oracle_hit - base_hit)}")

    # 单场双选命中率 vs 双选概率的校准（判断市场双选概率是否可信）
    print("\n  市场双选概率分桶 → 实际命中率（校准）:")
    edges = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    for i, lo in enumerate(edges):
        hi = edges[i+1] if i+1 < len(edges) else None
        m = df["pick_prob"] >= lo
        if hi is not None:
            m &= df["pick_prob"] < hi
        seg = df[m]
        if len(seg) == 0:
            continue
        rng = f"[{lo:.2f},{hi:.2f})" if hi is not None else f"[{lo:.2f},+]"
        print(f"    {rng:<12s} n={len(seg):5d}  市场概率均值={seg['pick_prob'].mean()*100:.1f}%  "
              f"实际命中={_pct(seg['hit'].mean())}")

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
