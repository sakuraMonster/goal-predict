"""TTG 进球数赔率「陷阱」挖掘 + 分梯度双选模型回测。

问题：竞彩总进球盘（0/1/2/3/4/5/6/7+ 八档）是否存在系统性定价偏差（陷阱）？
      即某些档位隐含概率系统性高估（诱盘）或低估（价值）。

方法：
  [A] 全局校准：每档「市场隐含概率 vs 实际频率」，找全局陷阱。
  [B] 市场 Top-2 基线（双选=市场隐含最高的 2 档）。
  [C] 分梯度校准：按市场隐含期望进球 λ 分 4 档，看每档内的陷阱（隐含 vs 实际）。
  [D] walk-forward 分梯度双选模型：每档用「历史实际频率」选 Top-2（修正市场偏差），
      对比市场 Top-2 的命中率与 ROI（双选 stake=2）。

全量 TTG 市场数据（含韩K 等所有联赛，不依赖 xG）。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_ttg_trap.py
"""
import os, sys, json, asyncio
from datetime import timedelta

import numpy as np
import pandas as pd
import asyncpg

REFIT_DAYS = 7
WARMUP_DAYS = 180

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

BUCKET_NAMES = {0: "0球", 1: "1球", 2: "2球", 3: "3球", 4: "4球", 5: "5球", 6: "6球", 7: "7+球"}


def ttg_to_probs_and_odds(j):
    """返回 (probs{0..7}, odds{0..7})。probs 为归一化隐含概率，odds 为原始赔率。"""
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
    probs = {k: v / s for k, v in inv.items()}
    return probs, odds


def lam_of(probs):
    return sum(k * probs.get(k, 0.0) for k in range(7)) + 7.0 * probs.get(7, 0.0)


def tier_of(lam):
    if lam < 2.0:
        return 0
    if lam < 2.5:
        return 1
    if lam < 3.0:
        return 2
    return 3


TIER_NAMES = {0: "低分 λ<2.0", 1: "中低 λ∈[2.0,2.5)", 2: "中高 λ∈[2.5,3.0)", 3: "高分 λ≥3.0"}


def top2(probs):
    return sorted(probs, key=lambda k: probs[k], reverse=True)[:2]


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

    parsed = df["ttg_odds_json"].apply(ttg_to_probs_and_odds)
    df["probs"] = parsed.apply(lambda x: x[0])
    df["odds"] = parsed.apply(lambda x: x[1])
    df = df[df["probs"].notna()].copy()
    df["lam"] = df["probs"].apply(lam_of)
    df["tier"] = df["lam"].apply(tier_of)

    print("=" * 88)
    print("TTG 进球数赔率「陷阱」挖掘 + 分梯度双选模型")
    print("=" * 88)
    print(f"已结算场次(含TTG赔率): {len(df)}")
    print(f"kickoff 时间窗: {df['kickoff_time'].min()} ~ {df['kickoff_time'].max()}")
    print(f"联赛数: {df['league_name'].nunique()}")

    # ── [A] 全局校准 ──
    print("\n[A] 全局校准：市场隐含概率 vs 实际频率（8 档）")
    print("-" * 88)
    print(f"{'档位':<8s}{'n':>6s}{'隐含':>8s}{'实际':>8s}{'偏差(隐含-实际)':>14s}")
    for k in range(8):
        imp = df["probs"].apply(lambda p: p.get(k, 0.0)).mean()
        act = (df["bucket"] == k).mean()
        print(f"{BUCKET_NAMES[k]:<8s}{int((df['bucket']==k).sum()):>6d}{imp*100:>7.1f}%{act*100:>7.1f}%{(imp-act)*100:>+13.1f}pp")

    # ── [B] 市场 Top-2 基线 ──
    mkt_top2 = df["probs"].apply(top2)
    mkt_hit = np.mean([int(df.loc[i, "bucket"] in mkt_top2[i]) for i in df.index])
    mkt_pay = [df.loc[i, "odds"].get(int(df.loc[i, "bucket"]), 0.0) if df.loc[i, "bucket"] in mkt_top2[i] else 0.0
               for i in df.index]
    mkt_roi = sum(mkt_pay) / (2 * len(df))
    print("\n[B] 市场 Top-2 双选基线（全量）")
    print(f"    命中率(实际∈市场Top2) = {_pct(mkt_hit)}   ROI(2注) = {mkt_roi:.4f}")

    # ── [C] 分梯度校准 ──
    print("\n[C] 分梯度校准：每档内 市场隐含 vs 实际（找分档陷阱）")
    print("-" * 88)
    for t in range(4):
        sub = df[df["tier"] == t]
        if len(sub) < 50:
            continue
        print(f"  {TIER_NAMES[t]}  (n={len(sub)})  市场λ均值={sub['lam'].mean():.2f}")
        print(f"    {'档位':<8s}{'隐含':>8s}{'实际':>8s}{'偏差':>12s}")
        for k in range(8):
            imp = sub["probs"].apply(lambda p: p.get(k, 0.0)).mean()
            act = (sub["bucket"] == k).mean()
            flag = "  <-- 高估(诱盘)" if imp - act > 0.03 else ("  <-- 低估(价值)" if act - imp > 0.03 else "")
            print(f"    {BUCKET_NAMES[k]:<8s}{imp*100:>7.1f}%{act*100:>7.1f}%{(imp-act)*100:>+11.1f}pp{flag}")

    # ── [D] walk-forward 分梯度双选模型 ──
    print("\n[D] walk-forward 分梯度双选模型（每档用历史实际频率选 Top-2）")
    print("-" * 88)
    min_dt = df["kickoff_time"].min()
    max_dt = df["kickoff_time"].max()
    refit_date = min_dt + timedelta(days=(WARMUP_DAYS // REFIT_DAYS) * REFIT_DAYS)

    res = []
    cur = refit_date
    while cur < max_dt:
        nxt = cur + timedelta(days=REFIT_DAYS)
        train = df[df["kickoff_time"] < cur]
        test = df[(df["kickoff_time"] >= cur) & (df["kickoff_time"] < nxt)]
        if len(train) < 500:
            cur = nxt
            continue

        # 每档的历史实际频率分布
        tier_freq = {}
        for t in range(4):
            sub = train[train["tier"] == t]
            if len(sub) < 100:
                continue
            freq = {k: float((sub["bucket"] == k).mean()) for k in range(8)}
            tier_freq[t] = freq

        for _, r in test.iterrows():
            t = r["tier"]
            if t not in tier_freq:
                continue
            emp_top2 = sorted(tier_freq[t], key=lambda k: tier_freq[t][k], reverse=True)[:2]
            res.append({
                "bucket": int(r["bucket"]),
                "tier": t,
                "mkt_top2": top2(r["probs"]),
                "emp_top2": emp_top2,
                "odds": r["odds"],
            })
        cur = nxt

    print(f"  评估场次: {len(res)}")
    if res:
        R = pd.DataFrame(res)
        # 市场 Top-2
        m_hit = np.mean([int(R.loc[i, "bucket"] in R.loc[i, "mkt_top2"]) for i in R.index])
        m_pay = [R.loc[i, "odds"].get(int(R.loc[i, "bucket"]), 0.0) if R.loc[i, "bucket"] in R.loc[i, "mkt_top2"] else 0.0
                 for i in R.index]
        m_roi = sum(m_pay) / (2 * len(R))
        # 分梯度经验 Top-2
        e_hit = np.mean([int(R.loc[i, "bucket"] in R.loc[i, "emp_top2"]) for i in R.index])
        e_pay = [R.loc[i, "odds"].get(int(R.loc[i, "bucket"]), 0.0) if R.loc[i, "bucket"] in R.loc[i, "emp_top2"] else 0.0
                 for i in R.index]
        e_roi = sum(e_pay) / (2 * len(R))
        print(f"    市场 Top-2 : 命中={_pct(m_hit)}  ROI={m_roi:.4f}")
        print(f"    分梯度Top-2: 命中={_pct(e_hit)}  ROI={e_roi:.4f}")

        # 各档命中差异
        print("\n    各档命中率对比（市场Top2 vs 分梯度经验Top2）:")
        print(f"    {'档位':<18s}{'n':>6s}{'市场Top2':>10s}{'经验Top2':>10s}")
        for t in range(4):
            sub = R[R["tier"] == t]
            if len(sub) < 30:
                continue
            mh = np.mean([int(sub.loc[i, "bucket"] in sub.loc[i, "mkt_top2"]) for i in sub.index])
            eh = np.mean([int(sub.loc[i, "bucket"] in sub.loc[i, "emp_top2"]) for i in sub.index])
            print(f"    {TIER_NAMES[t]:<18s}{len(sub):>6d}{_pct(mh):>10s}{_pct(eh):>10s}")

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
