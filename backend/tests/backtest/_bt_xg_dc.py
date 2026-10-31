"""xG vs 实际进球：Dixon-Coles 攻防强度估计的对比回测。

问题：football-math 框架主张用 xG（低方差）替代实际进球（高方差，~50% 运气）来估计
      球队攻防强度。本脚本在「同一训练集、不同响应变量」下公平对比：
        - goals-DC：响应 = 实际比分 (home_score, away_score)
        - xG-DC   ：响应 = 逐场 xG (home_xG, away_xG)，来自 SportMonks xGFixture
        - market  ：竞彩 TTG 隐含分布（现状市场信号）

训练集 = 有 xG 的已结算场次（仅韩K 外的联赛）；评估集 = 有 TTG 赔率的场次。
walk-forward 逐周重训，防泄露。

运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_xg_dc.py
（依赖 /tmp/xg_cache.json，先跑 /tmp/_fetch_league_xg.py）
"""
import os, sys, json, asyncio
from datetime import timedelta

import numpy as np
import pandas as pd
import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dixon_coles import DixonColes

LEAGUES = ["日职联", "西甲", "英超", "德甲", "意甲", "法甲"]
XG_CACHE = "/tmp/xg_cache.json"
REFIT_DAYS = 7
WARMUP_DAYS = 180
WARMUP_MIN_MATCHES = 1000
XI = 0.003
L2_REG = 0.5

SQL = """
SELECT
  m.id AS match_id,
  m.sportmonks_fixture_id AS fid,
  th.sportmonks_id AS home_smid,
  ta.sportmonks_id AS away_smid,
  m.home_score, m.away_score,
  m.kickoff_time,
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
  AND m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
  AND l.name_zh = ANY($1::text[])
ORDER BY m.kickoff_time ASC
"""


def ttg_to_probs(ttg_json):
    if ttg_json is None:
        return None
    if isinstance(ttg_json, str):
        try:
            ttg_json = json.loads(ttg_json)
        except (ValueError, TypeError):
            return None
    if not isinstance(ttg_json, dict):
        return None
    odds = {}
    for k, v in ttg_json.items():
        try:
            kk = int(k); oo = float(v)
        except (ValueError, TypeError):
            continue
        if oo > 1.0:
            odds[kk] = oo
    if not odds:
        return None
    inv = {k: 1.0 / o for k, o in odds.items()}
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()} if total > 0 else None


def cap_probs(dist):
    p = {k: 0.0 for k in range(8)}
    for k, v in dist.items():
        p[min(int(k), 7)] += float(v)
    s = sum(p.values())
    return {k: v / s for k, v in p.items()} if s > 0 else p


def p_over(probs):
    return sum(probs.get(k, 0.0) for k in range(3, 8))


def top3(probs):
    return sorted(probs, key=lambda k: probs[k], reverse=True)[:3]


def log_loss(probs, b):
    return -np.log(max(probs.get(b, 1e-9), 1e-9))


def _pct(x):
    return f"{x*100:5.1f}%"


async def main():
    cache = json.load(open(XG_CACHE)) if os.path.exists(XG_CACHE) else {}

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL, LEAGUES)
    await conn.close()

    df = pd.DataFrame([dict(r) for r in rows])
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"]).copy()
    df["total"] = (df["home_score"] + df["away_score"]).astype(int)
    df["bucket"] = df["total"].clip(upper=7)

    # 逐场 xG（来自缓存，按 home/away smid 匹配；fid/smid 可能被 pandas 转成 float，需还原为 int）
    def get_xg(r):
        fid = r["fid"]
        if fid is None or pd.isna(fid):
            return None, None
        m = cache.get(str(int(fid)))
        if not isinstance(m, dict):
            return None, None
        hs, as_ = r["home_smid"], r["away_smid"]
        if hs is None or as_ is None or pd.isna(hs) or pd.isna(as_):
            return None, None
        h = m.get(str(int(hs)))
        a = m.get(str(int(as_)))
        if h is None or a is None:
            return None, None
        return float(h), float(a)

    xg = df.apply(get_xg, axis=1, result_type="expand")
    df["home_xg"] = xg[0]; df["away_xg"] = xg[1]
    has_xg = df["home_xg"].notna() & df["away_xg"].notna()
    df["mkt"] = df["ttg_odds_json"].apply(ttg_to_probs)
    has_mkt = df["mkt"].notna()

    print("=" * 88)
    print("xG vs 实际进球：Dixon-Coles 攻防强度对比回测")
    print("=" * 88)
    print(f"目标联赛: {LEAGUES}")
    print(f"已结算场次           : {len(df)}")
    print(f"含逐场 xG(训练可用)  : {has_xg.sum()}  ({_pct(has_xg.mean())})")
    print(f"含 TTG 市场赔率      : {has_mkt.sum()}  ({_pct(has_mkt.mean())})")
    print(f"kickoff 时间窗       : {df['kickoff_time'].min()} ~ {df['kickoff_time'].max()}")

    min_dt = df["kickoff_time"].min()
    max_dt = df["kickoff_time"].max()
    refit_date = min_dt + timedelta(days=(WARMUP_DAYS // REFIT_DAYS) * REFIT_DAYS)

    results = []
    dc_goals = dc_xg = None
    cur = refit_date
    refit_count = 0
    while cur < max_dt:
        nxt = cur + timedelta(days=REFIT_DAYS)
        train = df[(df["kickoff_time"] < cur) & has_xg]
        test = df[(df["kickoff_time"] >= cur) & (df["kickoff_time"] < nxt)]

        if len(train) >= WARMUP_MIN_MATCHES and train["home_smid"].nunique() >= 15:
            try:
                # goals-DC：响应 = 实际比分
                dc_goals = DixonColes(xi=XI, l2_reg=L2_REG)
                dc_goals.fit(train["home_smid"].tolist(), train["away_smid"].tolist(),
                             train["home_score"].astype(float).tolist(),
                             train["away_score"].astype(float).tolist(),
                             train["kickoff_time"].tolist(), ref_date=cur)
                # xG-DC：响应 = 逐场 xG（同训练集）
                dc_xg = DixonColes(xi=XI, l2_reg=L2_REG)
                dc_xg.fit(train["home_smid"].tolist(), train["away_smid"].tolist(),
                          train["home_xg"].tolist(), train["away_xg"].tolist(),
                          train["kickoff_time"].tolist(), ref_date=cur)
                refit_count += 1
            except Exception as e:  # noqa
                print(f"  [warn] refit @ {cur.date()} 失败: {e}")

        if dc_goals is not None and dc_xg is not None and len(test):
            for _, r in test.iterrows():
                if r["mkt"] is None:
                    continue
                try:
                    g = cap_probs(dc_goals.predict(r["home_smid"], r["away_smid"])["total_dist"])
                    x = cap_probs(dc_xg.predict(r["home_smid"], r["away_smid"])["total_dist"])
                except Exception:
                    continue
                results.append({
                    "mkt": r["mkt"], "goals": g, "xg": x,
                    "bucket": int(r["bucket"]),
                    "odds": {int(k): float(v) for k, v in json.loads(r["ttg_odds_json"]).items()}
                    if isinstance(r["ttg_odds_json"], str) else {},
                })
        cur = nxt

    print(f"\n重训轮数: {refit_count}  |  评估场次: {len(results)}")
    if not results:
        print("无评估样本，终止")
        return

    R = pd.DataFrame(results)
    actual = R["bucket"].to_numpy()

    def report(label, col):
        po = R[col].apply(p_over).to_numpy()
        dir_hit = ((po >= 0.5) == (actual >= 3)).mean()
        t3 = np.mean([int(R.loc[i, "bucket"] in top3(R.loc[i, col])) for i in R.index])
        payouts = [R.loc[i, "odds"].get(int(R.loc[i, "bucket"]), 0.0)
                   if R.loc[i, "bucket"] in top3(R.loc[i, col]) else 0.0 for i in R.index]
        roi = sum(payouts) / (3 * len(R))
        ll = np.mean([log_loss(R.loc[i, col], R.loc[i, "bucket"]) for i in R.index])
        print(f"  {label:<10s} n={len(R):5d}  方向命中={_pct(dir_hit)}  Top3覆盖={_pct(t3)}  "
              f"ROI(3选)={roi:6.3f}  logloss={ll:.4f}")

    print("\n" + "-" * 88)
    print("[A] 三路对比（同训练集·不同响应）")
    print("-" * 88)
    report("market", "mkt")
    report("goals-DC", "goals")
    report("xG-DC", "xg")

    # 门控方向
    print("\n" + "-" * 88)
    print("[B] 方向门控命中率（|P_over - 0.5| 门槛）")
    print("-" * 88)
    for name, col in [("market", "mkt"), ("goals-DC", "goals"), ("xG-DC", "xg")]:
        po = R[col].apply(p_over)
        line = f"  {name:<10s}"
        for thr in [0.0, 0.08, 0.12, 0.18, 0.25]:
            mask = (po - 0.5).abs() >= thr
            if mask.sum() == 0:
                line += f"  |Δ|>={thr}: n/a"; continue
            hit = ((po[mask] >= 0.5) == (actual[mask] >= 3)).mean()
            line += f"  |Δ|>={thr}: {_pct(hit)}(n={mask.sum()})"
        print(line)

    # xG 与 goals 的分歧
    print("\n" + "-" * 88)
    print("[C] xG-DC vs goals-DC 方向分歧")
    print("-" * 88)
    pg = R["goals"].apply(p_over).to_numpy()
    px = R["xg"].apply(p_over).to_numpy()
    ao = actual >= 3
    for dg, dx, label in [(True, True, "both over"), (False, False, "both under"),
                          (True, False, "goals over / xg under"), (False, True, "goals under / xg over")]:
        mask = (pg >= 0.5) == dg
        mask &= (px >= 0.5) == dx
        n = int(mask.sum())
        if n == 0:
            continue
        gh = (((pg[mask] >= 0.5)) == ao[mask]).mean()
        xh = (((px[mask] >= 0.5)) == ao[mask]).mean()
        print(f"  {label:<24s} n={n:5d}  goals_hit={_pct(gh)}  xG_hit={_pct(xh)}")

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
