"""每日单场优选 —— 补充分析：双选组合命中差异 + 每天挑 K 场的命中曲线。

目的：
  [A] 双选组合（{1,2}/{2,3}/{3,4}）各自的实际命中率——看「1球低估」陷阱能否在单场优选里被利用。
  [B] 每天挑 Top-K 场的「当日命中率」曲线——量化「挑几场」这个杠杆。
  [C] 分联赛的单场双选命中率——看联赛是否是优选信号。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_daily_pick2.py
"""
import os, sys, json, asyncio
from datetime import timedelta

import numpy as np
import pandas as pd
import asyncpg

SQL = """
SELECT
  m.id AS match_id, m.home_score, m.away_score, m.kickoff_time,
  l.name_zh AS league_name, o.ttg_odds_json
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN LATERAL (
  SELECT js.ttg_odds_json FROM jczq_play_odds_snapshots js
  WHERE js.match_id = m.id AND js.ttg_odds_json IS NOT NULL
  ORDER BY js.snapshot_time DESC LIMIT 1
) o ON true
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL AND m.kickoff_time IS NOT NULL
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
    sub = {k: probs.get(k, 0.0) for k in [1, 2, 3, 4]}
    top2 = sorted(sub, key=lambda k: sub[k], reverse=True)[:2]
    return tuple(sorted(top2)), sum(sub[k] for k in top2)


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
    df["pair"] = picks.apply(lambda x: x[0])
    df["pick_prob"] = picks.apply(lambda x: x[1])
    df["hit"] = df.apply(lambda r: int(r["bucket"] in r["pair"]), axis=1)
    df["day"] = (df["kickoff_time"] - timedelta(hours=12)).dt.date

    print("=" * 88)
    print("每日单场优选 补充分析")
    print("=" * 88)

    # [A] 双选组合命中
    print("\n[A] 双选组合实际命中率（对应 1球低估陷阱）")
    print("-" * 88)
    print(f"  {'组合':<10s}{'n':>6s}{'市场概率均值':>12s}{'实际命中':>10s}{'偏差':>10s}")
    for pair in [(1,2),(2,3),(3,4)]:
        sub = df[df["pair"] == pair]
        if len(sub) == 0:
            continue
        imp = sub["pick_prob"].mean()
        act = sub["hit"].mean()
        print(f"  {str(pair):<10s}{len(sub):>6d}{imp*100:>11.1f}%{act*100:>9.1f}%{(imp-act)*100:>+9.1f}pp")

    # [B] Top-K 命中曲线
    print("\n[B] 每天挑 Top-K 场的当日命中率（K=1..5）")
    print("-" * 88)
    days = sorted(df["day"].unique())
    per_day = {d: df[df["day"] == d].sort_values("pick_prob", ascending=False) for d in days}
    for K in [1, 2, 3, 4, 5]:
        hits = 0
        for d in days:
            sub = per_day[d].head(K)
            hits += int(sub["hit"].max())  # 挑 K 场，任一命中即当日命中
        print(f"  Top-{K}: 当日命中率 = {_pct(hits/len(days))}  ({hits}/{len(days)} 天)")

    # [C] 分联赛
    print("\n[C] 分联赛单场双选命中率（top 12）")
    print("-" * 88)
    lg = df.groupby("league_name").agg(n=("hit", "size"), hit=("hit", "mean")).sort_values("n", ascending=False).head(12)
    for name, r in lg.iterrows():
        print(f"  {name:<8s} n={int(r['n']):>5d}  单场双选命中={_pct(r['hit'])}")

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
