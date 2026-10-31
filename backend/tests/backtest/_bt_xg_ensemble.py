"""市场 + xG-DC 的堆叠集成进球数模型回测。

思路（对应 Sumpter「统计思维」的延伸）：
  市场(TTG)是最强先验，xG-DC 是低方差基本面信号。不手调融合权重，
  而是用「堆叠 GLM」在 walk-forward 下学出二者的最优组合：

    logit(P_over_final) = β0 + β1·logit(P_mkt_over) + β2·logit(P_xG_over)

  β 由 sklearn LogisticRegression 在「历史 out-of-sample 预测」上每周重训（无泄露）。
  最终进球分布 = 市场分布按 P_over_final 做方向重标定（保市场形状，只校正大小球方向）。

三路对比：market(TTG) / xG-DC / ensemble(堆叠)。
诊断：market vs xG 方向分歧，谁更接近实际大球率。

运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_xg_ensemble.py
（依赖 /tmp/xg_cache.json）
"""
import os, sys, json, asyncio
from datetime import timedelta

import numpy as np
import pandas as pd
import asyncpg
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dixon_coles import DixonColes

LEAGUES = ["日职联", "西甲", "英超", "德甲", "意甲", "法甲"]
XG_CACHE = "/tmp/xg_cache.json"
REFIT_DAYS = 7
WARMUP_DAYS = 180
WARMUP_MIN_MATCHES = 1000
META_MIN = 200          # 堆叠模型最少历史样本
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


def logit(p):
    return float(np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6))))


def rescale_dist(mkt_dist, p_final_over):
    """把市场分布的方向（大/小）重标定到 p_final_over，保桶内相对形状。"""
    mkt_over = p_over(mkt_dist)
    mkt_under = 1.0 - mkt_over
    f_over = np.clip(p_final_over, 1e-6, 1 - 1e-6)
    f_under = 1.0 - f_over
    new = {}
    for k in range(8):
        base = mkt_dist.get(k, 0.0)
        if k <= 2:
            new[k] = base * (f_under / mkt_under) if mkt_under > 1e-6 else base
        else:
            new[k] = base * (f_over / mkt_over) if mkt_over > 1e-6 else base
    s = sum(new.values())
    return {k: v / s for k, v in new.items()} if s > 0 else new


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
        h = m.get(str(int(hs))); a = m.get(str(int(as_)))
        if h is None or a is None:
            return None, None
        return float(h), float(a)

    xg = df.apply(get_xg, axis=1, result_type="expand")
    df["home_xg"] = xg[0]; df["away_xg"] = xg[1]
    has_xg = df["home_xg"].notna() & df["away_xg"].notna()
    df["mkt"] = df["ttg_odds_json"].apply(ttg_to_probs)

    print("=" * 88)
    print("市场 + xG-DC 堆叠集成进球数模型回测")
    print("=" * 88)
    print(f"联赛: {LEAGUES}")
    print(f"已结算: {len(df)}  含xG: {has_xg.sum()}({_pct(has_xg.mean())})  含TTG: {df['mkt'].notna().sum()}({_pct(df['mkt'].notna().mean())})")

    min_dt = df["kickoff_time"].min()
    max_dt = df["kickoff_time"].max()
    refit_date = min_dt + timedelta(days=(WARMUP_DAYS // REFIT_DAYS) * REFIT_DAYS)

    hist = []            # 历史 out-of-sample 预测：{lm, lx, actual_over, mkt, xg, bucket, odds}
    dc = None
    cur = refit_date
    refit_count = 0
    coefs = []

    while cur < max_dt:
        nxt = cur + timedelta(days=REFIT_DAYS)
        train = df[(df["kickoff_time"] < cur) & has_xg]
        test = df[(df["kickoff_time"] >= cur) & (df["kickoff_time"] < nxt)]

        if len(train) >= WARMUP_MIN_MATCHES and train["home_smid"].nunique() >= 15:
            try:
                dc = DixonColes(xi=XI, l2_reg=L2_REG)
                dc.fit(train["home_smid"].tolist(), train["away_smid"].tolist(),
                       train["home_xg"].tolist(), train["away_xg"].tolist(),
                       train["kickoff_time"].tolist(), ref_date=cur)
                refit_count += 1
            except Exception as e:  # noqa
                print(f"  [warn] refit @ {cur.date()} 失败: {e}")

        # 用历史 out-of-sample 样本训堆叠 GLM
        meta = None
        if len(hist) >= META_MIN:
            X = np.array([[h["lm"], h["lx"]] for h in hist])
            y = np.array([h["actual_over"] for h in hist]).astype(int)
            meta = LogisticRegression(C=1.0, max_iter=1000)
            meta.fit(X, y)
            coefs.append((meta.intercept_[0], meta.coef_[0][0], meta.coef_[0][1]))

        if dc is not None and len(test):
            for _, r in test.iterrows():
                if r["mkt"] is None:
                    continue
                try:
                    xg_dist = cap_probs(dc.predict(r["home_smid"], r["away_smid"])["total_dist"])
                except Exception:
                    continue
                mkt_dist = r["mkt"]
                pm = p_over(mkt_dist)
                px = p_over(xg_dist)
                lm = logit(pm); lx = logit(px)

                if meta is not None:
                    pf = float(meta.predict_proba([[lm, lx]])[0, 1])
                else:
                    pf = pm
                final_dist = rescale_dist(mkt_dist, pf)

                hist.append({
                    "lm": lm, "lx": lx,
                    "actual_over": int(r["bucket"] >= 3),
                    "mkt": mkt_dist, "xg": xg_dist, "final": final_dist,
                    "bucket": int(r["bucket"]),
                    "odds": {int(k): float(v) for k, v in json.loads(r["ttg_odds_json"]).items()}
                    if isinstance(r["ttg_odds_json"], str) else {},
                })
        cur = nxt

    print(f"\n重训轮数: {refit_count}  堆叠样本: {len(hist)}")
    if not hist:
        print("无样本，终止"); return
    H = pd.DataFrame(hist)
    actual = H["bucket"].to_numpy()

    if coefs:
        c = np.array(coefs)
        print(f"堆叠GLM系数(均值): β0={c[:,0].mean():+.3f}  β1(mkt)={c[:,1].mean():+.3f}  β2(xg)={c[:,2].mean():+.3f}")

    def report(label, col):
        po = H[col].apply(p_over).to_numpy()
        dir_hit = ((po >= 0.5) == (actual >= 3)).mean()
        t3 = np.mean([int(H.loc[i, "bucket"] in top3(H.loc[i, col])) for i in H.index])
        pay = [H.loc[i, "odds"].get(int(H.loc[i, "bucket"]), 0.0)
               if H.loc[i, "bucket"] in top3(H.loc[i, col]) else 0.0 for i in H.index]
        roi = sum(pay) / (3 * len(H))
        ll = np.mean([log_loss(H.loc[i, col], H.loc[i, "bucket"]) for i in H.index])
        print(f"  {label:<10s} n={len(H):5d}  方向={_pct(dir_hit)}  Top3={_pct(t3)}  ROI={roi:6.3f}  logloss={ll:.4f}")

    print("\n[A] 三路对比")
    print("-" * 88)
    report("market", "mkt")
    report("xG-DC", "xg")
    report("ensemble", "final")

    # 诊断：market vs xG 方向分歧，谁更接近实际
    print("\n[B] 诊断：market vs xG 方向分歧（实际大球率 vs 各自预测）")
    print("-" * 88)
    pm = H["mkt"].apply(p_over).to_numpy()
    px = H["xg"].apply(p_over).to_numpy()
    ao = (actual >= 3).astype(int)
    for dm, dx, label in [(True, True, "both over"), (False, False, "both under"),
                          (True, False, "mkt over / xg under"), (False, True, "mkt under / xg over")]:
        mask = ((pm >= 0.5) == dm) & ((px >= 0.5) == dx)
        n = int(mask.sum())
        if n == 0:
            continue
        print(f"  {label:<24s} n={n:5d}  实际大球率={_pct(ao[mask].mean())}  "
              f"mkt预测={_pct(pm[mask].mean())}  xg预测={_pct(px[mask].mean())}")

    # 门控方向
    print("\n[C] 方向门控（|P_over-0.5| 门槛）")
    print("-" * 88)
    for name, col in [("market", "mkt"), ("xG-DC", "xg"), ("ensemble", "final")]:
        po = H[col].apply(p_over)
        line = f"  {name:<10s}"
        for thr in [0.0, 0.08, 0.12, 0.18, 0.25]:
            m = (po - 0.5).abs() >= thr
            if m.sum() == 0:
                line += f"  |Δ|>={thr}: n/a"; continue
            hit = ((po[m] >= 0.5) == (actual[m] >= 3)).mean()
            line += f"  |Δ|>={thr}: {_pct(hit)}(n={m.sum()})"
        print(line)

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
