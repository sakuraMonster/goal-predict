"""动态双选 + 赔率≥3.3 约束 —— 每日选 2 场命中率/ROI（近三个月）。

口径：
  - 动态双选 = 从 {1,2,3,4} 取隐含最高的 2 档（方案E 现行逻辑）。
  - 赔率约束 = 所选两档的赔率都 ≥3.3；不满足则跳过该场。
  - 每日按双选概率挑 2 场，任一中即当日命中；ROI = Σ返奖/Σ注数。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_dyn_odds.py
"""
import os, sys, json, asyncio, datetime
from datetime import timedelta

import pandas as pd
import asyncpg

DATE_FROM = "2026-07-22"
ODD_MIN = 3.3

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
    # 双选两档赔率都≥3.3 才算通过
    df["pass_odds"] = df.apply(
        lambda r: all(r["odds"].get(int(k), 0.0) >= ODD_MIN for k in r["dyn_nums"]), axis=1)
    df["day"] = (df["kickoff_time"] - timedelta(hours=12)).dt.date

    days = sorted(df["day"].unique())

    def eval_strategy(mask, label):
        hit_days = 0
        stake = 0.0
        payout = 0.0
        used = 0
        for d in days:
            sub = df[(df["day"] == d) & mask].sort_values("dyn_prob", ascending=False).head(2)
            if len(sub) == 0:
                continue
            day_hit = False
            for _, r in sub.iterrows():
                used += 1
                stake += 2.0
                if int(r["bucket"]) in r["dyn_nums"]:
                    day_hit = True
                    payout += r["odds"].get(int(r["bucket"]), 0.0)
            if day_hit:
                hit_days += 1
        roi = payout / stake if stake else 0.0
        return hit_days, len(days), roi, used

    print("=" * 88)
    print(f"动态双选 + 赔率≥{ODD_MIN} 约束（近三个月 {df['day'].min()} ~ {df['day'].max()}）")
    print("=" * 88)
    print(f"比赛日: {len(days)}  场次: {len(df)}")
    print(f"双选两档赔率都≥{ODD_MIN}: {int(df['pass_odds'].sum())}  ({_pct(df['pass_odds'].mean())})")
    print(f"双选组合分布: " + ", ".join(
        f"{p}:{int((df['dyn_nums']==p).sum())}" for p in [(1,2),(2,3),(3,4)]))

    print("\n" + "-" * 88)
    print("每日选 2 场（任一中）:")
    print("-" * 88)
    h, n, r, u = eval_strategy(pd.Series(True, index=df.index), "动态双选(无过滤)")
    print(f"  动态双选(无过滤) : 命中 {h}/{n} = {_pct(h/n)}   ROI = {r:.4f}  (用场{u})")
    h, n, r, u = eval_strategy(df["pass_odds"], f"动态双选+赔率≥{ODD_MIN}")
    print(f"  动态双选+赔率≥{ODD_MIN}: 命中 {h}/{n} = {_pct(h/n)}   ROI = {r:.4f}  (用场{u})")

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
