"""Dixon-Coles 进球数模型回测（离线一次性脚本，运行于服务器容器内）。

评估口径（对齐竞彩「进球数」玩法 0/1/2/3/4/5/6/7+ 八档）：
  1. 大小球方向（O/U 2.5）命中率（全量 + 门控）
  2. 进球数 Top-3 选覆盖（p_hit = 实际进球 ∈ 选中的 3 档）
  3. Top-3 复式 ROI（用竞彩 TTG 实际赔率）
  4. 对数损失 log-loss（严格评分，判断谁预测更准）

三路对比：
  - market   ：竞彩 TTG 隐含分布（= 现状市场信号）
  - dc       ：纯基本面 Dixon-Coles（本模型）
  - fused    ：Dixon-Coles 与市场做几何均值后融合（p_fund^w * p_mkt^(1-w)）

防泄露：walk-forward 逐周重训，仅用 kickoff < 重训日 的历史；市场赔率取开赛前最新快照。

运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/_bt_dixon_coles.py
"""
import os
import sys
import json
import asyncio
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import asyncpg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dixon_coles import DixonColes

# ───────────────────────── 参数 ─────────────────────────
REFIT_DAYS = 7            # 每 7 天重训一次
WARMUP_DAYS = 180         # 至少 180 天历史才开始预测
WARMUP_MIN_MATCHES = 3000 # 训练集最少场次
XI = 0.003                # 时间衰减（半衰期 ~231 天）
L2_REG = 0.5              # L2 收缩强度
FUSION_WEIGHTS = [0.0, 0.25, 0.5, 0.75, 1.0]  # w=0 纯市场, w=1 纯基本面

SQL = """
SELECT
  m.id AS match_id,
  m.home_team_id,
  m.away_team_id,
  m.home_score,
  m.away_score,
  m.kickoff_time,
  m.league_id,
  l.name_zh AS league_name,
  o.ttg_odds_json
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN LATERAL (
  SELECT js.ttg_odds_json
  FROM jczq_play_odds_snapshots js
  WHERE js.match_id = m.id AND js.ttg_odds_json IS NOT NULL
  ORDER BY js.snapshot_time DESC
  LIMIT 1
) o ON true
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
  AND m.kickoff_time IS NOT NULL
ORDER BY m.kickoff_time ASC
"""


# ───────────────────────── 工具 ─────────────────────────
def ttg_to_probs(ttg_json):
    """TTG 赔率 JSON -> 归一化隐含分布 {0..7: p}，7 = 「7+」"""
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
            kk = int(k)
            oo = float(v)
        except (ValueError, TypeError):
            continue
        if oo > 1.0:
            odds[kk] = oo
    if not odds:
        return None
    inv = {k: 1.0 / o for k, o in odds.items()}
    total = sum(inv.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in inv.items()}


def cap_probs(total_dist):
    """把 0..19 的全分布压到 0..7+ 八档（竞彩口径）。"""
    p = {k: 0.0 for k in range(8)}
    for k, v in total_dist.items():
        p[min(int(k), 7)] += float(v)
    s = sum(p.values())
    return {k: v / s for k, v in p.items()} if s > 0 else p


def p_over(probs):
    """P(总进球 >= 3) = P(大 2.5 球)"""
    return sum(probs.get(k, 0.0) for k in range(3, 8))


def top3(probs):
    return sorted(probs, key=lambda k: probs[k], reverse=True)[:3]


def fuse(p_fund, p_mkt, w):
    """几何均值融合（log-linear pooling），w=基本面权重。"""
    keys = set(p_fund) | set(p_mkt)
    fused = {}
    for k in keys:
        fused[k] = (max(p_fund.get(k, 1e-9), 1e-9) ** w) * \
                   (max(p_mkt.get(k, 1e-9), 1e-9) ** (1 - w))
    z = sum(fused.values())
    return {k: v / z for k, v in fused.items()}


def log_loss(probs, actual_bucket):
    p = max(probs.get(actual_bucket, 1e-9), 1e-9)
    return -np.log(p)


def _fmt_pct(x):
    return f"{x*100:5.1f}%"


# ───────────────────────── 回测主流程 ─────────────────────────
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
    df["bucket"] = df["total"].clip(upper=7)  # 竞彩 7+ 档

    # 市场分布
    df["mkt"] = df["ttg_odds_json"].apply(ttg_to_probs)
    has_mkt = df["mkt"].notna()

    print("=" * 88)
    print("Dixon-Coles 进球数模型回测  (walk-forward 逐周重训)")
    print("=" * 88)
    print(f"已结算场次(含双方team) : {len(df)}")
    print(f"含 TTG 市场赔率         : {has_mkt.sum()}  ({_fmt_pct(has_mkt.mean())})")
    print(f"kickoff 时间窗          : {df['kickoff_time'].min()} ~ {df['kickoff_time'].max()}")
    print(f"联赛覆盖               : {df['league_name'].nunique()} 个联赛")
    print(f"参数: xi={XI}, l2_reg={L2_REG}, refit={REFIT_DAYS}d, warmup={WARMUP_DAYS}d/{WARMUP_MIN_MATCHES}场")

    # walk-forward
    min_dt = df["kickoff_time"].min()
    max_dt = df["kickoff_time"].max()

    results = []          # 每个被评估场次：{mkt_probs, dc_probs, actual_bucket, odds}
    dc_stats = []         # 每轮模型的诊断
    refit_count = 0

    refit_date = min_dt + timedelta(days=WARMUP_DAYS)
    # 对齐到 REFIT_DAYS 的整数倍（从 min_dt 起算）
    refit_date = min_dt + timedelta(days=(WARMUP_DAYS // REFIT_DAYS) * REFIT_DAYS)

    dc = None
    cur = refit_date
    while cur < max_dt:
        nxt = cur + timedelta(days=REFIT_DAYS)
        train = df[df["kickoff_time"] < cur]
        test = df[(df["kickoff_time"] >= cur) & (df["kickoff_time"] < nxt)]

        if len(train) >= WARMUP_MIN_MATCHES and train["home_team_id"].nunique() >= 20:
            try:
                dc = DixonColes(xi=XI, l2_reg=L2_REG)
                dc.fit(
                    train["home_team_id"].tolist(),
                    train["away_team_id"].tolist(),
                    train["home_score"].astype(int).tolist(),
                    train["away_score"].astype(int).tolist(),
                    train["kickoff_time"].tolist(),
                    ref_date=cur,
                )
                refit_count += 1
                dc_stats.append({
                    "refit_date": cur,
                    "n_train": len(train),
                    "n_teams": len(dc._team_index),
                    "mu": dc.mu, "home_adv": dc.home_adv, "rho": dc.rho,
                })
            except Exception as e:  # noqa
                print(f"  [warn] refit @ {cur.date()} 失败: {e}，沿用上一轮模型")

        if dc is not None and len(test):
            for _, r in test.iterrows():
                if r["mkt"] is None:
                    continue
                try:
                    dc_out = dc.predict(r["home_team_id"], r["away_team_id"])
                    dc_probs = cap_probs(dc_out["total_dist"])
                except Exception:
                    continue
                # 实际赔率（用于 ROI）
                odds_map = json.loads(r["ttg_odds_json"]) if isinstance(r["ttg_odds_json"], str) \
                    else r["ttg_odds_json"]
                results.append({
                    "mkt": r["mkt"],
                    "dc": dc_probs,
                    "bucket": int(r["bucket"]),
                    "odds": {int(k): float(v) for k, v in odds_map.items()} if isinstance(odds_map, dict) else {},
                })

        cur = nxt

    print(f"\n重训轮数: {refit_count}  |  评估场次(有市场赔率): {len(results)}")
    if not results:
        print("无评估样本，终止")
        return

    R = pd.DataFrame(results)
    actual = R["bucket"].to_numpy()

    # 诊断
    if dc_stats:
        s = pd.DataFrame(dc_stats)
        print(f"\n模型诊断 (均值): mu={s['mu'].mean():+.3f}  home_adv={s['home_adv'].mean():+.3f}  rho={s['rho'].mean():+.4f}")
        print(f"  球队数范围: {s['n_teams'].min()}~{s['n_teams'].max()}  训练场次范围: {s['n_train'].min()}~{s['n_train'].max()}")

    # ── 评估 ──
    def eval_row(row, probs):
        """返回 (dir_hit, top3_hit, roi_payout, logloss)"""
        p_over_val = p_over(probs)
        dir_pred = p_over_val >= 0.5
        dir_hit = int(dir_pred == (row["bucket"] >= 3))
        sel = top3(probs)
        top3_hit = int(row["bucket"] in sel)
        odds = row["odds"].get(int(row["bucket"]), 0.0)
        roi_payout = odds if row["bucket"] in sel else 0.0
        ll = log_loss(probs, row["bucket"])
        return dir_hit, top3_hit, roi_payout, ll

    def report(label, probs_col):
        dir_hits, t3_hits, payouts, lls = [], [], [], []
        for _, row in R.iterrows():
            dh, t3, pay, ll = eval_row(row, row[probs_col])
            dir_hits.append(dh); t3_hits.append(t3); payouts.append(pay); lls.append(ll)
        dir_hits = np.array(dir_hits); t3_hits = np.array(t3_hits)
        payouts = np.array(payouts); lls = np.array(lls)
        n = len(R)
        stake = 3 * n
        roi = payouts.sum() / stake if stake else float("nan")
        print(f"  {label:<10s} n={n:5d}  方向命中={_fmt_pct(dir_hits.mean())}  "
              f"Top3覆盖={_fmt_pct(t3_hits.mean())}  ROI(3选复式)={roi:6.3f}  "
              f"logloss={lls.mean():.4f}")
        return dir_hits, t3_hits, payouts, lls

    print("\n" + "-" * 88)
    print("[A] 三路对比（全量评估场次）")
    print("-" * 88)
    report("market", "mkt")
    report("dc", "dc")

    # 融合权重扫描
    print("\n" + "-" * 88)
    print("[B] 融合权重扫描  (w=0 纯市场 / w=1 纯基本面)")
    print("-" * 88)
    for w in FUSION_WEIGHTS:
        if w == 0.0:
            R["_f"] = R["mkt"]
        elif w == 1.0:
            R["_f"] = R["dc"]
        else:
            R["_f"] = [fuse(r["dc"], r["mkt"], w) for _, r in R.iterrows()]
        report(f"w={w:g}", "_f")

    # 门控方向分析（对 dc 与 market，按 |P_over-0.5| 门槛）
    print("\n" + "-" * 88)
    print("[C] 大小球方向门控命中率（|P_over - 0.5| 门槛）")
    print("-" * 88)
    for name, col in [("market", "mkt"), ("dc", "dc")]:
        po = R[col].apply(p_over)
        line = f"  {name:<10s}"
        for thr in [0.0, 0.08, 0.12, 0.18, 0.25]:
            mask = (po - 0.5).abs() >= thr
            if mask.sum() == 0:
                line += f"  |Δ|>={thr}: n/a"
                continue
            hit = ((po[mask] >= 0.5) == (actual[mask] >= 3)).mean()
            line += f"  |Δ|>={thr}: {_fmt_pct(hit)}(n={mask.sum()})"
        print(line)

    # 门控后融合方向（w=0.5）
    po_f = R.apply(lambda r: p_over(fuse(r["dc"], r["mkt"], 0.5)), axis=1)
    line = "  fused w=0.5"
    for thr in [0.0, 0.08, 0.12, 0.18, 0.25]:
        mask = (po_f - 0.5).abs() >= thr
        if mask.sum() == 0:
            line += f"  |Δ|>={thr}: n/a"
            continue
        hit = ((po_f[mask] >= 0.5) == (actual[mask] >= 3)).mean()
        line += f"  |Δ|>={thr}: {_fmt_pct(hit)}(n={mask.sum()})"
    print(line)

    # 分歧分析：基本面与市场方向不一致时，谁更准（框架镜片 6）
    print("\n" + "-" * 88)
    print("[D] 基本面 vs 市场 方向分歧分析（O/U 2.5）")
    print("-" * 88)
    po_mkt = R["mkt"].apply(p_over).to_numpy()
    po_dc = R["dc"].apply(p_over).to_numpy()
    actual_over = (actual >= 3)
    d_mkt = po_mkt >= 0.5
    d_dc = po_dc >= 0.5
    for dm, dd, label in [
        (True, True, "both over"),
        (False, False, "both under"),
        (True, False, "mkt over / dc under"),
        (False, True, "mkt under / dc over"),
    ]:
        mask = (d_mkt == dm) & (d_dc == dd)
        n = int(mask.sum())
        if n == 0:
            continue
        mkt_hit = (d_mkt[mask] == actual_over[mask]).mean()
        dc_hit = (d_dc[mask] == actual_over[mask]).mean()
        print(f"  {label:<24s} n={n:5d}  market_hit={_fmt_pct(mkt_hit)}  dc_hit={_fmt_pct(dc_hit)}")

    # 强分歧（|P_over 差| >= 0.15）：基本面是否在极端背离时有独立信息
    print("\n  强分歧（|P_over_dc - P_over_mkt| >= 0.15）：")
    gap = np.abs(po_dc - po_mkt)
    mask = gap >= 0.15
    n = int(mask.sum())
    if n:
        mkt_hit = (d_mkt[mask] == actual_over[mask]).mean()
        dc_hit = (d_dc[mask] == actual_over[mask]).mean()
        print(f"    n={n:5d}  market_hit={_fmt_pct(mkt_hit)}  dc_hit={_fmt_pct(dc_hit)}  "
              f"(基本面偏离市场时，其方向是否仍可信)")
        # 更细：基本面更看大 vs 市场
        sub = mask & (po_dc > po_mkt)
        if sub.sum():
            print(f"      dc 更看大  n={int(sub.sum()):5d}  market_hit={_fmt_pct((d_mkt[sub]==actual_over[sub]).mean())}  dc_hit={_fmt_pct((d_dc[sub]==actual_over[sub]).mean())}")
        sub = mask & (po_dc < po_mkt)
        if sub.sum():
            print(f"      dc 更看小  n={int(sub.sum()):5d}  market_hit={_fmt_pct((d_mkt[sub]==actual_over[sub]).mean())}  dc_hit={_fmt_pct((d_dc[sub]==actual_over[sub]).mean())}")
    else:
        print("    n=0")

    print("\n" + "=" * 88)
    print("done")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
