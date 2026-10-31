"""固定 {2,3} 球 + 单个赔率≥3.3 过滤 —— 每日选 2 场命中率/ROI（近三个月）。

口径：
  - 候选 = 2球赔率≥3.3 且 3球赔率≥3.3 的场次。
  - 每日选 2 场：按 P(2∪3) 降序（或按赔率），任一中即当日命中。
  - ROI = Σ返奖 / Σ注数（每场 2 注，命中按实际档赔率返奖）。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_23_odds.py
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

    # 2/3 球数据
    df["o2"] = df["odds"].apply(lambda o: o.get(2))
    df["o3"] = df["odds"].apply(lambda o: o.get(3))
    df["p23"] = df["probs"].apply(lambda p: p.get(2, 0.0) + p.get(3, 0.0))
    df["has23"] = df["o2"].notna() & df["o3"].notna()
    df["pass_odds"] = (df["o2"] >= ODD_MIN) & (df["o3"] >= ODD_MIN)
    df["day"] = (df["kickoff_time"] - timedelta(hours=12)).dt.date

    days = sorted(df["day"].unique())

    def eval_strategy(mask, rank_col, label):
        hit_days = 0
        stake = 0.0
        payout = 0.0
        used = 0
        for d in days:
            sub = df[(df["day"] == d) & mask].sort_values(rank_col, ascending=False).head(2)
            if len(sub) == 0:
                continue
            day_hit = False
            for _, r in sub.iterrows():
                used += 1
                stake += 2.0
                if int(r["bucket"]) in (2, 3):
                    day_hit = True
                    payout += r["odds"].get(int(r["bucket"]), 0.0)
            if day_hit:
                hit_days += 1
        roi = payout / stake if stake else 0.0
        return hit_days, len(days), roi, used

    print("=" * 88)
    print(f"{2,3} 球 + 赔率≥{ODD_MIN} 过滤（近三个月 {df['day'].min()} ~ {df['day'].max()}）")
    print("=" * 88)
    print(f"比赛日: {len(days)}  场次: {len(df)}")
    print(f"有 2/3 赔率: {int(df['has23'].sum())}  通过赔率≥{ODD_MIN}: {int(df['pass_odds'].sum())}  "
          f"({_pct(df['pass_odds'].mean())})")

    # 单场命中参考
    all23 = df[df["has23"]]
    pass23 = df[df["pass_odds"]]
    print(f"\n单场命中参考: 全量{2,3} {_pct((all23['bucket'].isin([2,3])).mean())}  "
          f"赔率≥{ODD_MIN} {_pct((pass23['bucket'].isin([2,3])).mean())}")
    print(f"  全量 2球均赔={all23['o2'].mean():.2f} 3球均赔={all23['o3'].mean():.2f}  "
          f"过滤后 2球均赔={pass23['o2'].mean():.2f} 3球均赔={pass23['o3'].mean():.2f}")

    print("\n" + "-" * 88)
    print("每日选 2 场（任一中）:")
    print("-" * 88)

    h, n, r, u = eval_strategy(df["has23"], "p23", f"全量{2,3}·按P23排")
    print(f"  全量{2,3}·按P23排     : 命中 {h}/{n} = {_pct(h/n)}   ROI = {r:.4f}  (用场{u})")

    h, n, r, u = eval_strategy(df["pass_odds"], "p23", f"赔率≥{ODD_MIN}·按P23排")
    print(f"  赔率≥{ODD_MIN}·按P23排 : 命中 {h}/{n} = {_pct(h/n)}   ROI = {r:.4f}  (用场{u})")

    # 按赔率高低排（最高赔率优先）
    df["o_sum"] = df["o2"] + df["o3"]
    h, n, r, u = eval_strategy(df["pass_odds"], "o_sum", f"赔率≥{ODD_MIN}·按赔率排")
    print(f"  赔率≥{ODD_MIN}·按赔率排 : 命中 {h}/{n} = {_pct(h/n)}   ROI = {r:.4f}  (用场{u})")

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
