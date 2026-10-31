"""固定 3/4 球 vs 动态双选 —— 每日选 2 场命中率/ROI 对比（近三个月）。

问题：动态双选几乎总是 {2,3}。若固定选 {3,4} 两档、每天按 P(3∪4) 挑 2 场，
      当日命中率（任一中）与 ROI 能否追上动态双选？

口径：
  - 动态双选：从 {1,2,3,4} 取隐含最高 2 档，按双选概率挑 2 场。
  - 固定 34 ：只买 {3,4} 两档，按 P(3)+P(4) 挑 2 场。
  - 当日命中 = 任一场命中其选择档；ROI = Σ返奖 / Σ注数（每场 2 注）。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_fixed34.py
"""
import os, sys, json, asyncio, datetime
from datetime import timedelta

import numpy as np
import pandas as pd
import asyncpg

DATE_FROM = "2026-07-22"

SQL = """
SELECT m.id AS match_id, m.home_score, m.away_score, m.kickoff_time,
       l.name_zh AS league_name, o.ttg_odds_json
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN LATERAL (
  SELECT js.ttg_odds_json FROM jczq_play_odds_snapshots js
  WHERE js.match_id = m.id AND js.ttg_odds_json IS NOT NULL
  ORDER BY js.snapshot_time DESC LIMIT 1
) o ON true
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND m.kickoff_time >= $1::date
ORDER BY m.kickoff_time ASC
"""


def ttg_to_probs_and_odds(j):
    if j is None:
        return None, None
    if isinstance(j, str):
        try:
            j = json.loads(j)
        except (ValueError, TypeError):
            return None, None
    if not isinstance(j, dict):
        return None, None
    odds = {}
    for k, v in j.items():
        try:
            kk = int(k); oo = float(v)
        except (ValueError, TypeError):
            continue
        if oo > 1.0:
            odds[kk] = oo
    if not odds:
        return None, None
    inv = {k: 1.0 / o for k, o in odds.items()}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s > 0 else None, odds


def dyn_pick(probs):
    sub = {k: probs.get(k, 0.0) for k in (1, 2, 3, 4)}
    top2 = sorted(sub, key=lambda k: sub[k], reverse=True)[:2]
    return tuple(sorted(top2)), sub[top2[0]] + sub[top2[1]]


def _pct(x):
    return f"{x*100:5.1f}%"


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL, datetime.date.fromisoformat(DATE_FROM))
    await conn.close()

    df = pd.DataFrame([dict(r) for r in rows])
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"]).copy()
    df["total"] = (df["home_score"] + df["away_score"]).astype(int)
    df["bucket"] = df["total"].clip(upper=7)

    parsed = df["ttg_odds_json"].apply(ttg_to_probs_and_odds)
    df["probs"] = parsed.apply(lambda x: x[0])
    df["odds"] = parsed.apply(lambda x: x[1])
    df = df[df["probs"].notna()].copy()

    dp = df["probs"].apply(dyn_pick)
    df["dyn_nums"] = dp.apply(lambda x: x[0])
    df["dyn_prob"] = dp.apply(lambda x: x[1])
    df["p34"] = df["probs"].apply(lambda p: p.get(3, 0.0) + p.get(4, 0.0))
    df["day"] = (df["kickoff_time"] - timedelta(hours=12)).dt.date

    days = sorted(df["day"].unique())

    def eval_strategy(rank_col, nums_of, label):
        """按 rank_col 挑每日 top-2，命中 = 实际 ∈ nums_of(该场)，返回 (命中天数, ROI)。"""
        hit_days = 0
        stake = 0.0
        payout = 0.0
        for d in days:
            sub = df[df["day"] == d].sort_values(rank_col, ascending=False).head(2)
            day_hit = False
            for _, r in sub.iterrows():
                nums = nums_of(r)
                stake += 2.0
                if int(r["bucket"]) in nums:
                    day_hit = True
                    payout += r["odds"].get(int(r["bucket"]), 0.0)
            if day_hit:
                hit_days += 1
        roi = payout / stake if stake else 0.0
        return hit_days, len(days), roi

    h1, n1, r1 = eval_strategy("dyn_prob", lambda r: r["dyn_nums"], "动态双选")
    h2, n2, r2 = eval_strategy("p34", lambda r: (3, 4), "固定34")

    print("=" * 88)
    print(f"固定 3/4 球 vs 动态双选 —— 每日选 2 场（近三个月 {df['day'].min()} ~ {df['day'].max()}）")
    print("=" * 88)
    print(f"比赛日: {len(days)}  场次: {len(df)}")

    # 单场命中率参考
    dyn_hit = df.apply(lambda r: int(r["bucket"] in r["dyn_nums"]), axis=1).mean()
    f34_hit = df["bucket"].isin([3, 4]).mean()
    print(f"\n单场命中率参考: 动态双选 {_pct(dyn_hit)}  固定{3,4} {_pct(f34_hit)}")

    print("\n" + "-" * 88)
    print("每日选 2 场（任一中）:")
    print("-" * 88)
    print(f"  动态双选: 当日命中 {h1}/{n1} = {_pct(h1/n1)}   ROI = {r1:.4f}")
    print(f"  固定34 : 当日命中 {h2}/{n2} = {_pct(h2/n2)}   ROI = {r2:.4f}")

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
