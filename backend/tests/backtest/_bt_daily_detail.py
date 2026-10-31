"""每日押一场 / 押两场 —— 近三个月逐日输出（选择场次 + 命中情况）。

口径：
  - 双选 = 从 {1,2,3,4} 取市场隐含概率最高的 2 档。
  - 押一场：每天选「双选概率最高」的 1 场，2 注（2 档各 1 注）。
  - 押两场：每天选「双选概率前 2」的 2 场，4 注；任一场命中即「当日命中」。
  - ROI = Σ返奖 / Σ注数（命中注按该档实际赔率返奖，未中注返 0）。

运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_daily_detail.py
"""
import os, sys, json, asyncio, datetime
from datetime import timedelta

import numpy as np
import pandas as pd
import asyncpg

DATE_FROM = "2026-07-22"

SQL = """
SELECT
  m.id AS match_id, m.home_score, m.away_score, m.kickoff_time,
  COALESCE(th.name_zh, m.home_team_name) AS home_name,
  COALESCE(ta.name_zh, m.away_team_name) AS away_name,
  l.name_zh AS league_name,
  o.ttg_odds_json
FROM matches m
LEFT JOIN teams th ON th.id = m.home_team_id
LEFT JOIN teams ta ON ta.id = m.away_team_id
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
    if s <= 0:
        return None, None
    return {k: v / s for k, v in inv.items()}, odds


def double_pick(probs):
    sub = {k: probs.get(k, 0.0) for k in [1, 2, 3, 4]}
    top2 = sorted(sub, key=lambda k: sub[k], reverse=True)[:2]
    return tuple(sorted(top2)), sum(sub[k] for k in top2)


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

    picks = df["probs"].apply(double_pick)
    df["pair"] = picks.apply(lambda x: x[0])
    df["pick_prob"] = picks.apply(lambda x: x[1])
    df["hit"] = df.apply(lambda r: int(r["bucket"] in r["pair"]), axis=1)
    df["payout"] = df.apply(lambda r: r["odds"].get(int(r["bucket"]), 0.0) if r["hit"] else 0.0, axis=1)
    df["day"] = (df["kickoff_time"] - timedelta(hours=12)).dt.date

    days = sorted(df["day"].unique())

    print("=" * 96)
    print(f"每日押一场 vs 押两场（近三个月 {df['day'].min()} ~ {df['day'].max()}）")
    print("=" * 96)
    print(f"比赛日: {len(days)}  场次: {len(df)}")
    print()
    print(f"{'日期':<12}{'序':<3}{'比赛(主 vs 客)':<24}{'联赛':<6}{'双选':<8}{'概率':>6}{'实际':>5}  结果")

    one_hit = 0
    two_hit = 0
    one_stake = 0
    two_stake = 0
    one_payout = 0.0
    two_payout = 0.0

    for d in days:
        sub = df[df["day"] == d].sort_values("pick_prob", ascending=False)
        top1 = sub.iloc[0]
        top2 = sub.iloc[1] if len(sub) >= 2 else None

        # 押一场
        one_stake += 2
        one_payout += top1["payout"]
        if top1["hit"]:
            one_hit += 1

        # 押两场（任一中）
        if top2 is not None:
            two_stake += 4
            two_payout += top1["payout"] + top2["payout"]
            if top1["hit"] or top2["hit"]:
                two_hit += 1
        else:
            two_stake += 2
            two_payout += top1["payout"]
            if top1["hit"]:
                two_hit += 1

        def fmt(r, idx):
            mark = "✓ 命中" if r["hit"] else "✗ 未中"
            return (f"{str(d):<12}[{idx}] {str(r['home_name'])[:11]} vs {str(r['away_name'])[:10]:<10}"
                    f"{str(r['league_name'])[:5]:<6}{str(r['pair']):<8}"
                    f"{r['pick_prob']*100:>5.1f}% {int(r['total']):>3}球  {mark}")

        print(fmt(top1, 1))
        if top2 is not None:
            print(fmt(top2, 2))

    print()
    print("=" * 96)
    print("汇总")
    print("=" * 96)
    print(f"押一场: 当日命中 {one_hit}/{len(days)} = {one_hit/len(days)*100:.1f}%   "
          f"ROI = {one_payout/one_stake:.4f}  (返奖{one_payout:.1f}/下注{one_stake})")
    print(f"押两场: 当日命中 {two_hit}/{len(days)} = {two_hit/len(days)*100:.1f}%   "
          f"ROI = {two_payout/two_stake:.4f}  (返奖{two_payout:.1f}/下注{two_stake})")
    print("=" * 96)


if __name__ == "__main__":
    asyncio.run(main())
