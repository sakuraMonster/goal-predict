"""固定 {1,3} 球 vs 动态双选 —— 每日选 2 场命中率/ROI 对比（近三个月）。

含「排除 2球众数场次」过滤（近似去掉 1:1平/2球倾向场），看能否提升 {1,3} 命中。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_fixed13.py
"""
import os, sys, json, asyncio, datetime
from datetime import timedelta

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
    df["p13"] = df["probs"].apply(lambda p: p.get(1, 0.0) + p.get(3, 0.0))
    df["p2"] = df["probs"].apply(lambda p: p.get(2, 0.0))
    # 2球是否众数（市场最看 2球）
    df["p2_is_mode"] = df["probs"].apply(lambda p: (p.get(2, 0.0) >= max(p.get(k, 0.0) for k in range(8))))
    df["day"] = (df["kickoff_time"] - timedelta(hours=12)).dt.date

    days = sorted(df["day"].unique())

    def eval_strategy(rank_col, nums_of, mask=None, label=""):
        hit_days = 0
        stake = 0.0
        payout = 0.0
        for d in days:
            sub = df[df["day"] == d]
            if mask is not None:
                sub = sub[mask]
            sub = sub.sort_values(rank_col, ascending=False).head(2)
            if len(sub) == 0:
                continue
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

    # 单场命中率参考
    ref = {}
    for name, nums in [("动态", None), ("{1,3}", (1, 3)), ("{2,3}", (2, 3)), ("{3,4}", (3, 4))]:
        if name == "动态":
            ref[name] = df.apply(lambda r: int(r["bucket"] in r["dyn_nums"]), axis=1).mean()
        else:
            ref[name] = df["bucket"].isin(list(nums)).mean()

    print("=" * 88)
    print(f"{1,3} 球 vs 动态双选 —— 每日选 2 场（近三个月 {df['day'].min()} ~ {df['day'].max()}）")
    print("=" * 88)
    print(f"比赛日: {len(days)}  场次: {len(df)}")
    print(f"\n单场命中率参考: 动态 {_pct(ref['动态'])}  {1,3} {_pct(ref['{1,3}'])}  "
          f"{2,3} {_pct(ref['{2,3}'])}  {3,4} {_pct(ref['{3,4}'])}")

    print("\n" + "-" * 88)
    print("每日选 2 场（任一中）:")
    print("-" * 88)

    h, n, r = eval_strategy("dyn_prob", lambda r: r["dyn_nums"])
    print(f"  动态双选         : 命中 {h}/{n} = {_pct(h/n)}   ROI = {r:.4f}")

    h, n, r = eval_strategy("p13", lambda r: (1, 3))
    print(f"  固定 {1,3}        : 命中 {h}/{n} = {_pct(h/n)}   ROI = {r:.4f}")

    # 排除 2球众数场次
    h, n, r = eval_strategy("p13", lambda r: (1, 3), mask=~df["p2_is_mode"])
    print(f"  固定 {1,3}·去2球众数: 命中 {h}/{n} = {_pct(h/n)}   ROI = {r:.4f}")

    # 排除 P(2球)>=0.26
    mask2 = df["p2"] < 0.26
    h, n, r = eval_strategy("p13", lambda r: (1, 3), mask=mask2)
    print(f"  固定 {1,3}·去P2≥.26: 命中 {h}/{n} = {_pct(h/n)}   ROI = {r:.4f}")

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
