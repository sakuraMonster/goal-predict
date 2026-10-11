"""盘口异动 / 赔率异常区间「情报模型」离线回测（一次性脚本）。

来源：知乎 tao哥 文章用「让球方胜赔 × 平赔」的异常区间预测「让球方不胜」。
本脚本在真实生产库（已结算比赛）上检验其可迁移性，并测试两个新特征：
  特征1 draw_anomaly : 平赔隐含概率相对「同档让方强度应有平赔」的残差
                       （+ = 平赔被拉低/平局隐含偏高；- = 平赔被顶高）
  特征2 depth × fav   : 让球深度 × 让方强度 的交互
  特征3 movement      : 开盘→收盘的赔率异动（文章只有终盘单点，我们有多快照）

目标变量（统一口径）：
  fav_cover      = 让球方让胜（让球后赢盘）  -> 文章核心反面 = fav_not_cover（让方不胜）
  hcp_draw       = 让球后打平
  fav_outright_not_win = 让球方不赢球（未让球口径）

数据口径：JczqPlayOddsSnapshot（竞彩 HAD/HHAD 终盘）+ Match 实际比分。
运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt.py
"""
import os
import asyncio
from math import sqrt

import numpy as np
import pandas as pd
import asyncpg

SQL = """
SELECT
  m.id AS match_id,
  m.kickoff_time::date::text AS matchday,
  l.name_zh AS league,
  m.home_score,
  m.away_score,
  s.snapshot_time::text AS snap_time,
  s.had_home, s.had_draw, s.had_away,
  s.hhad_line, s.hhad_home, s.hhad_draw, s.hhad_away
FROM matches m
JOIN jczq_play_odds_snapshots s ON s.match_id = m.id
LEFT JOIN leagues l ON l.id = m.league_id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND s.had_home IS NOT NULL AND s.had_draw IS NOT NULL AND s.had_away IS NOT NULL
ORDER BY m.id, s.snapshot_time
"""

SQL_V2 = """
SELECT mfp.match_id,
       mfp.pool_freeze_json->>'preferred_outcome' AS pref,
       mfp.pool_freeze_json->>'pool' AS pool,
       mfp.pool_freeze_json->>'fav' AS v2_fav,
       (mfp.pool_freeze_json->>'fav_ip')::float AS fav_ip
FROM market_flow_predictions mfp
"""


def pct(x):
    if x is None:
        return "  n/a"
    try:
        if isinstance(x, float) and np.isnan(x):
            return "  n/a"
    except Exception:
        pass
    return f"{x*100:5.1f}%"


def z2(h1, n1, h2, n2):
    if n1 == 0 or n2 == 0:
        return float("nan")
    p = (h1 + h2) / (n1 + n2)
    se = sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (h1 / n1 - h2 / n2) / se if se > 0 else float("nan")


def line(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def build():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = asyncio.get_event_loop().run_until_complete(asyncpg.connect(dsn))
    rows = asyncio.get_event_loop().run_until_complete(conn.fetch(SQL))
    v2rows = asyncio.get_event_loop().run_until_complete(conn.fetch(SQL_V2))
    asyncio.get_event_loop().run_until_complete(conn.close())
    df = pd.DataFrame([dict(r) for r in rows])
    v2 = pd.DataFrame([dict(r) for r in v2rows])
    return df, v2


def main():
    raw, v2 = build()
    raw["snap_time"] = pd.to_datetime(raw["snap_time"])
    raw = raw.sort_values(["match_id", "snap_time"])

    n_rows = len(raw)
    n_matches = raw["match_id"].nunique()
    cnt = raw.groupby("match_id").size()
    line("0. 数据概况")
    print(f"  总快照行数={n_rows}  去重比赛数={n_matches}")
    print(f"  有>=2个快照的比赛数={int((cnt >= 2).sum())}（可做开盘->收盘异动）")
    print(f"  单快照比赛数={int((cnt == 1).sum())}")

    # 终盘 = 每场最后一条快照
    close = raw.groupby("match_id").tail(1).copy()
    open_ = raw.groupby("match_id").head(1).copy()

    inv = 1 / close["had_home"] + 1 / close["had_draw"] + 1 / close["had_away"]
    close["p_h"] = 1 / close["had_home"] / inv
    close["p_d"] = 1 / close["had_draw"] / inv
    close["p_a"] = 1 / close["had_away"] / inv

    close["line"] = close["hhad_line"]
    close["fav"] = np.where(close["line"] < 0, "home",
                            np.where(close["line"] > 0, "away", None))
    c = close[close["fav"].notna()].copy()   # 排除无让球方
    print(f"  有明确让球方的终盘比赛数={len(c)}")

    c["fav_odds"] = np.where(c["fav"] == "home", c["had_home"], c["had_away"])
    c["dog_odds"] = np.where(c["fav"] == "home", c["had_away"], c["had_home"])
    c["p_fav"] = np.where(c["fav"] == "home", c["p_h"], c["p_a"])
    c["p_dog"] = np.where(c["fav"] == "home", c["p_a"], c["p_h"])
    c["draw_odds"] = c["had_draw"]

    # 让球后（主队视角）结果：adj_home = home_score + hhad_line
    adj_home = c["home_score"] + c["line"]
    home_hcp = np.select([adj_home > c["away_score"], adj_home < c["away_score"]],
                         [1, -1], default=0)
    c["fav_cover"] = np.where(c["fav"] == "home", home_hcp == 1, home_hcp == -1)
    c["fav_not_cover"] = ~c["fav_cover"]
    c["hcp_draw"] = home_hcp == 0

    out = np.select([c["home_score"] > c["away_score"], c["home_score"] < c["away_score"]],
                    [1, -1], default=0)
    c["fav_outright_win"] = np.where(c["fav"] == "home", out == 1, out == -1)
    c["fav_outright_not_win"] = ~c["fav_outright_win"]

    c["depth"] = c["line"].abs()

    print(f"  基线：fav_cover={pct(c['fav_cover'].mean())} "
          f"fav_not_cover={pct(c['fav_not_cover'].mean())} "
          f"hcp_draw={pct(c['hcp_draw'].mean())} "
          f"fav_outright_not_win={pct(c['fav_outright_not_win'].mean())}")
    print(f"  日期范围：{c['matchday'].min()} ~ {c['matchday'].max()}")
    print("  让球线分布：")
    for v, n in c["line"].value_counts().sort_index().items():
        print(f"    line={v:+.1f}  n={n}")

    # ---------- 特征1: draw_anomaly ----------
    line("1. 特征1 draw_anomaly：平赔隐含概率对「让方强度」的残差")
    c = c.sort_values("matchday").reset_index(drop=True)
    split = int(len(c) * 0.7)
    tr, te = c.iloc[:split], c.iloc[split:]
    coef = np.polyfit(tr["p_fav"], tr["p_d"], 2)  # 训练段拟合 E[p_d | p_fav]
    c["p_d_exp"] = np.polyval(coef, c["p_fav"])
    c["draw_anom"] = c["p_d"] - c["p_d_exp"]
    print(f"  train n={len(tr)}  test n={len(te)}  拟合 p_d ~ quad(p_fav)：{coef}")
    print(f"  平赔隐含 p_d 均值={c['p_d'].mean():.3f}  "
          f"残差 mean={c['draw_anom'].mean():+.4f} sd={c['draw_anom'].std():.4f}")

    c["anom_q"] = pd.qcut(c["draw_anom"].rank(method="first"), 5, labels=False)
    print("\n  按残差五分位（Q1=平赔最被顶高，Q5=平赔最被拉低）：")
    print(f"    {'桶':>4} {'n':>6} {'anom中位':>10} {'fav_cover':>10} "
          f"{'fav_not_cover':>13} {'hcp_draw':>10} {'赔率不下盘':>12}")
    for q in range(5):
        s = c[c["anom_q"] == q]
        print(f"    Q{q+1:<3} {len(s):>6} {s['draw_anom'].median():>+10.4f} "
              f"{pct(s['fav_cover'].mean()):>10} {pct(s['fav_not_cover'].mean()):>13} "
              f"{pct(s['hcp_draw'].mean()):>10} {pct(s['fav_outright_not_win'].mean()):>12}")
    q1, q5 = c[c["anom_q"] == 0], c[c["anom_q"] == 4]
    zz = z2(int(q5["fav_not_cover"].sum()), len(q5),
            int(q1["fav_not_cover"].sum()), len(q1))
    print(f"  Q5 vs Q1 (fav_not_cover) z={zz:+.2f}   "
          f"{pct(q5['fav_not_cover'].mean())} vs {pct(q1['fav_not_cover'].mean())}")

    # 仅测试段（out-of-sample）重复一次
    te2 = c.iloc[split:].copy()
    if len(te2) > 50:
        te2["q2"] = pd.qcut(te2["draw_anom"].rank(method="first"), 4, labels=False)
        print("\n  [out-of-sample test段] 四分位 fav_not_cover：")
        for q in range(4):
            s = te2[te2["q2"] == q]
            print(f"    Q{q+1}  n={len(s):>5}  anom中位={s['draw_anom'].median():+.4f}  "
                  f"fav_not_cover={pct(s['fav_not_cover'].mean())}  "
                  f"hcp_draw={pct(s['hcp_draw'].mean())}")

    # ---------- 复现文章三类规则 ----------
    line("2. 复现知乎文章规则（让方胜赔区间 × 平赔阈值）")
    print(f"  {'fav_odds':>14} {'平赔条件':>12} {'n':>6} {'fav_cover':>10} "
          f"{'fav_not_cover':>13} {'hcp_draw':>10}")

    def seg(mask, favlabel, drawwlabel):
        s = c[mask]
        if len(s) == 0:
            print(f"  {favlabel:>14} {drawwlabel:>12} {0:>6}")
            return
        print(f"  {favlabel:>14} {drawwlabel:>12} {len(s):>6} "
              f"{pct(s['fav_cover'].mean()):>10} {pct(s['fav_not_cover'].mean()):>13} "
              f"{pct(s['hcp_draw'].mean()):>10}")

    # ① 平赔异常低
    seg((c["fav_odds"].between(1.40, 1.50)) & (c["draw_odds"] < 4.0), "1.40-1.50", "<4.0")
    seg((c["fav_odds"].between(1.90, 2.00)) & (c["draw_odds"] <= 3.45), "1.90-2.00", "<=3.45")
    # ③ 平赔异常高
    seg((c["fav_odds"].between(2.00, 2.10)) & (c["draw_odds"] > 3.30), "2.00-2.10", ">3.30")
    seg((c["fav_odds"].between(2.40, 2.50)) & (c["draw_odds"] > 3.20), "2.40-2.50", ">3.20")
    # ① 深让步
    seg(c["fav_odds"] < 1.14, "<1.14", "(any)")
    seg(c["depth"] >= 2, "(depth>=2)", "(any)")

    print("\n  让方胜赔分段（不看平赔）的 fav_not_cover：")
    edges = [0, 1.30, 1.50, 1.70, 1.90, 2.10, 2.40, 2.80, 99]
    for i in range(len(edges) - 1):
        m = c["fav_odds"] >= edges[i]
        m &= c["fav_odds"] < edges[i + 1]
        s = c[m]
        if len(s) == 0:
            continue
        print(f"    [{edges[i]:.2f},{edges[i+1]:.2f})  n={len(s):>5}  "
              f"fav_not_cover={pct(s['fav_not_cover'].mean())}  "
              f"hcp_draw={pct(s['hcp_draw'].mean())}")

    # 平赔二维：fav_odds 段 × 平赔段
    print("\n  二维表 fav_not_cover (行=fav_odds段, 列=平赔段)：")
    fedges = [0, 1.60, 1.90, 2.20, 99]
    dedges = [0, 3.20, 3.50, 3.90, 99]
    hdr = "          " + "".join([f"[{dedges[j]:.2f},{dedges[j+1]:.2f})".rjust(16)
                                  for j in range(len(dedges) - 1)])
    print(hdr)
    for i in range(len(fedges) - 1):
        row = f"  [{fedges[i]:.2f},{fedges[i+1]:.2f})".rjust(10)
        for j in range(len(dedges) - 1):
            m = c["fav_odds"] >= fedges[i]
            m &= c["fav_odds"] < fedges[i + 1]
            m &= c["draw_odds"] >= dedges[j]
            m &= c["draw_odds"] < dedges[j + 1]
            s = c[m]
            row += (f"{pct(s['fav_not_cover'].mean())}({len(s)})".rjust(16)
                    if len(s) else "-".rjust(16))
        print(row)

    # ---------- 特征2: 深度 × 强度 ----------
    line("3. 特征2 让球深度 × 让方强度 交互（fav_not_cover）")
    dlabels = [(0.99, 1.01, "1"), (1.51, 99, "2+")]
    flabels = [(0, 0.40, "<.40"), (0.40, 0.50, ".40-.50"),
               (0.50, 0.60, ".50-.60"), (0.60, 0.75, ".60-.75"), (0.75, 1.01, ">=.75")]
    hdr = "  深度\\fav_ip " + "".join([l[2].rjust(12) for l in flabels])
    print(hdr)
    for lo, hi, dl in dlabels:
        row = f"  {dl:>10} "
        for flo, fhi, fl in flabels:
            s = c[(c["depth"] >= lo) & (c["depth"] < hi)
                  & (c["p_fav"] >= flo) & (c["p_fav"] < fhi)]
            row += (f"{pct(s['fav_not_cover'].mean())}({len(s)})".rjust(12)
                    if len(s) else "-".rjust(12))
        print(row)

    # ---------- 特征3: 开盘->收盘异动 ----------
    line("4. 特征3 开盘->收盘赔率异动（仅>=2快照）")
    ids_multi = cnt[cnt >= 2].index
    o = open_[open_["match_id"].isin(ids_multi)].copy()
    cl = close[close["match_id"].isin(ids_multi)].copy()
    for d in (o, cl):
        invv = 1 / d["had_home"] + 1 / d["had_draw"] + 1 / d["had_away"]
        d["p_h"] = 1 / d["had_home"] / invv
        d["p_d"] = 1 / d["had_draw"] / invv
        d["p_a"] = 1 / d["had_away"] / invv
    o = o[["match_id", "p_h", "p_d", "p_a", "had_home", "had_draw", "had_away"]]
    cl = cl[["match_id", "p_h", "p_d", "p_a"]]
    mg = o.merge(cl, on="match_id", suffixes=("_o", "_c"))
    mg["fav"] = np.where(mg["had_home"] < mg["had_away"], "home", "away")
    mg["d_pd"] = mg["p_d_c"] - mg["p_d_o"]
    mg["d_fav"] = np.where(mg["fav"] == "home",
                           mg["p_h_c"] - mg["p_h_o"], mg["p_a_c"] - mg["p_a_o"])
    mgc = c.merge(mg[["match_id", "d_pd", "d_fav"]], on="match_id", how="inner")
    print(f"  可算异动的比赛数={len(mgc)}  "
          f"d_pd sd={mgc['d_pd'].std():.4f}  d_fav sd={mgc['d_fav'].std():.4f}")
    # 平赔隐含上升 vs 下降
    for lbl, m in [("平赔隐含↑(平赔降)", mgc["d_pd"] > 0.005),
                   ("基本不动", mgc["d_pd"].abs() <= 0.005),
                   ("平赔隐含↓(平赔升)", mgc["d_pd"] < -0.005)]:
        s = mgc[m]
        print(f"    {lbl:>16}  n={len(s):>5}  fav_not_cover={pct(s['fav_not_cover'].mean())}  "
              f"hcp_draw={pct(s['hcp_draw'].mean())}")
    # 与 draw_anom 相关性
    if len(mgc) > 30:
        r = np.corrcoef(mgc["d_pd"], mgc["draw_anom"])[0, 1]
        rr = np.corrcoef(mgc["d_pd"], mgc["fav_not_cover"].astype(float))[0, 1]
        print(f"    corr(d_pd, draw_anom)={r:+.4f}   corr(d_pd, fav_not_cover)={rr:+.4f}")

    # ---------- 增量 vs MarketFlow V2 ----------
    line("5. 增量检验 vs MarketFlow V2（preferred_outcome / pool）")
    v = v2.dropna(subset=["pref"]).copy()
    v = v[["match_id", "pref", "pool", "v2_fav", "fav_ip"]].drop_duplicates("match_id")
    j = c.merge(v, on="match_id", how="inner")
    print(f"  与 V2 预测可对齐的比赛数={len(j)}")
    if len(j):
        print("  V2 pool 分布：")
        for k, n in j["pool"].value_counts().items():
            print(f"    pool={k}  n={n}")
        print("\n  V2 preferred_outcome 分布 & 实际 fav_not_cover：")
        for k, s in j.groupby("pref"):
            print(f"    pref={k:<8} n={len(s):>5}  "
                  f"实际fav_not_cover={pct(s['fav_not_cover'].mean())}  "
                  f"hcp_draw={pct(s['hcp_draw'].mean())}")
        # V2 判正路(让方胜)时，draw_anom 是否还能进一步区分
        pos = j[j["pref"] == j["fav"]]
        if len(pos) > 50:
            pos = pos.copy()
            pos["hi_anom"] = pos["draw_anom"] > pos["draw_anom"].median()
            a = pos[pos["hi_anom"]]
            b = pos[~pos["hi_anom"]]
            print(f"\n  [V2判正路 pref==fav n={len(pos)}] 用 draw_anom 切分：")
            print(f"    平赔偏高残差(>中位)  n={len(a):>5}  实际fav_cover={pct(a['fav_cover'].mean())}  "
                  f"fav_not_cover={pct(a['fav_not_cover'].mean())}")
            print(f"    平赔偏低残差(<=中位) n={len(b):>5}  实际fav_cover={pct(b['fav_cover'].mean())}  "
                  f"fav_not_cover={pct(b['fav_not_cover'].mean())}")
            if len(a) and len(b):
                zz = z2(int(a["fav_not_cover"].sum()), len(a),
                        int(b["fav_not_cover"].sum()), len(b))
                print(f"    z={zz:+.2f}")

    line("6. 「让方不胜」2注复式 EV 检验（命中率是否转化为 +EV）")
    # 买 让平 + 让负(让方盘口输)：2 注复式，命中返还对应单项赔率，成本 2
    loss_odds = np.where(c["fav"] == "home", c["hhad_away"], c["hhad_home"])
    ret = np.where(c["hcp_draw"], c["hhad_draw"],
                   np.where(c["fav_cover"], 0.0, loss_odds))
    c["ret_2bet"] = ret
    c["roi_2bet"] = ret / 2.0
    print(f"  全样本 n={len(c)}  「让方不胜」2注复式 ROI(返奖/下注)="
          f"{c['roi_2bet'].mean():.3f}  （>1 才为正收益）")
    print(f"    其中 让平单项赔率均值={c['hhad_draw'].mean():.2f}  "
          f"让负单项赔率均值={np.nanmean(loss_odds):.2f}")

    print("\n  按 draw_anom 五分位的「让方不胜」2注复式 ROI：")
    print(f"    {'桶':>4} {'n':>6} {'命中率':>8} {'ROI':>8} {'均赔(命中腿)':>14}")
    for q in range(5):
        s = c[c["anom_q"] == q]
        hit_odds = s.loc[s["ret_2bet"] > 0, "ret_2bet"]
        print(f"    Q{q+1:<3} {len(s):>6} {pct(s['fav_not_cover'].mean()):>8} "
              f"{s['roi_2bet'].mean():>8.3f} {(hit_odds.mean() if len(hit_odds) else float('nan')):>14.2f}")

    print("\n  方向拆分（对照文章 ②低平赔 / ③高平赔）：")
    for lbl, m in [("Q1 平赔最被顶高(anom最低)", c["anom_q"] == 0),
                   ("Q5 平赔最被拉低(anom最高)", c["anom_q"] == 4)]:
        s = c[m]
        print(f"    {lbl:<24} n={len(s):>5}  "
              f"让平命中={pct(s['hcp_draw'].mean())}  "
              f"让方盘口输={pct((~s['fav_cover'] & ~s['hcp_draw']).mean())}  "
              f"ROI={s['roi_2bet'].mean():.3f}")

    # 文章规则②的代表桶的 ROI
    for lbl, m in [("1.90-2.00 & 平赔<=3.45", (c["fav_odds"].between(1.90, 2.00)) & (c["draw_odds"] <= 3.45)),
                   ("1.40-1.50 & 平赔<4.0", (c["fav_odds"].between(1.40, 1.50)) & (c["draw_odds"] < 4.0)),
                   ("2.40-2.50 & 平赔>3.20", (c["fav_odds"].between(2.40, 2.50)) & (c["draw_odds"] > 3.20))]:
        s = c[m]
        if len(s):
            print(f"    文章桶[{lbl}]  n={len(s):>4}  命中={pct(s['fav_not_cover'].mean())}  "
                  f"ROI={s['roi_2bet'].mean():.3f}")

    # ---------- 分赔率档的「平赔异常」规则扫描（供情报规则注册表） ----------
    line("7. 分赔率档扫描「平赔异常」规则（让方不胜率 / 样本量）")
    bands = [(1.01, 1.30), (1.30, 1.50), (1.50, 1.70), (1.70, 1.90),
             (1.90, 2.10), (2.10, 2.40), (2.40, 99)]
    print(f"  {'fav_odds档':>13} {'n':>5} {'档base':>8} | "
          f"{'平赔低位(P33)':>22} | {'平赔高位(P67)':>22}")
    for lo, hi in bands:
        s = c[(c["fav_odds"] >= lo) & (c["fav_odds"] < hi)]
        if len(s) < 30:
            print(f"  [{lo:.2f},{hi:.2f})  n={len(s)} (skip)")
            continue
        p33 = s["draw_odds"].quantile(0.33)
        p67 = s["draw_odds"].quantile(0.67)
        low = s[s["draw_odds"] <= p33]
        high = s[s["draw_odds"] >= p67]
        print(f"  [{lo:.2f},{hi:.2f})  n={len(s):>5} {pct(s['fav_not_cover'].mean()):>8} | "
              f"≤{p33:.2f} {pct(low['fav_not_cover'].mean())}(n{len(low)}) | "
              f"≥{p67:.2f} {pct(high['fav_not_cover'].mean())}(n{len(high)})")

    # 每档的低平赔组 z 检验（与同档其余比）
    print("\n  各档「平赔<=P33」组的 z 检验（vs 同档其余）：")
    for lo, hi in bands:
        s = c[(c["fav_odds"] >= lo) & (c["fav_odds"] < hi)]
        if len(s) < 30:
            continue
        thr = s["draw_odds"].quantile(0.33)
        low = s[s["draw_odds"] <= thr]
        rest = s[s["draw_odds"] > thr]
        zz = z2(int(low["fav_not_cover"].sum()), len(low),
                int(rest["fav_not_cover"].sum()), len(rest))
        print(f"  [{lo:.2f},{hi:.2f})  平赔<={thr:.2f}  n={len(low):>4}  "
              f"不胜={pct(low['fav_not_cover'].mean())}  vs 其余 {pct(rest['fav_not_cover'].mean())}  z={zz:+.2f}")

    # 绝对阈值版（便于写成固定规则）：平赔 < 3.20 / 3.50，按档
    print("\n  绝对阈值版（各档 平赔<阈值 的让方不胜率）：")
    for thr in (3.20, 3.50):
        row = f"    平赔<{thr:.2f}: "
        for lo, hi in bands:
            s = c[(c["fav_odds"] >= lo) & (c["fav_odds"] < hi) & (c["draw_odds"] < thr)]
            row += f"[{lo:.2f}-{hi:.2f})={pct(s['fav_not_cover'].mean())}(n{len(s)})  " if len(s) >= 20 else f"[{lo:.2f}-{hi:.2f})=-- "
        print(row)

    # ---------- 最终情报规则注册表评估（待编码，供生产展示 sample 数字） ----------
    line("8. 最终情报规则注册表评估（让方不胜率 / 样本量 / z）")
    # (code, 名称, fav_lo, fav_hi, 平赔上限)  —— 档位互斥，一场至多命中一条
    RULES = [
        ("I1", "强热门·平赔被拉低", 1.30, 1.50, 4.00),
        ("I2", "中热门·平赔被拉低", 1.50, 1.70, 3.55),
        ("I3", "中热门·平赔被拉低", 1.70, 1.90, 3.30),
        ("I4", "热门·平赔被拉低", 1.90, 2.10, 3.10),
        ("I5", "温和热门·平赔被拉低", 2.10, 2.40, 3.00),
        ("I6", "冷门档·平赔被拉低", 2.40, 99.0, 2.95),
    ]
    print(f"  {'code':>5} {'名称':<18} {'条件':<30} {'n':>5} {'让方不胜':>9} "
          f"{'档base':>8} {'lift':>7} {'z':>6}")
    any_mask = pd.Series(False, index=c.index)
    for code, name, lo, hi, dmax in RULES:
        band = c[(c["fav_odds"] >= lo) & (c["fav_odds"] < hi)]
        s = band[band["draw_odds"] < dmax]
        if len(s) == 0:
            continue
        m = (c["fav_odds"] >= lo) & (c["fav_odds"] < hi) & (c["draw_odds"] < dmax)
        any_mask = any_mask | m
        hb = int(band["fav_not_cover"].sum()) - int(s["fav_not_cover"].sum())
        nb = len(band) - len(s)
        zz = z2(int(s["fav_not_cover"].sum()), len(s), hb, nb)
        cond = f"fav∈[{lo:.2f},{hi:.2f}) & 平赔<{dmax:.2f}"
        print(f"  {code:>5} {name:<18} {cond:<30} {len(s):>5} "
              f"{pct(s['fav_not_cover'].mean()):>9} {pct(band['fav_not_cover'].mean()):>8} "
              f"{(s['fav_not_cover'].mean()-band['fav_not_cover'].mean())*100:>+6.1f} {zz:>+6.2f}")

    u = c[any_mask]
    rest = c[~any_mask]
    zu = z2(int(u["fav_not_cover"].sum()), len(u), int(rest["fav_not_cover"].sum()), len(rest))
    print(f"\n  命中任一规则：n={len(u)}  n占比={len(u)/len(c)*100:.1f}%  "
          f"让方不胜={pct(u['fav_not_cover'].mean())} vs 未命中 {pct(rest['fav_not_cover'].mean())}  z={zu:+.2f}")
    # 前后两段稳定性
    md = pd.to_datetime(c["matchday"])

    def _rule_mask(seg):
        fav = seg["fav_odds"]
        dr = seg["draw_odds"]
        thr = pd.Series(3.00, index=seg.index)
        thr = thr.mask(fav < 2.10, 3.10).mask(fav < 1.90, 3.30).mask(fav < 1.70, 3.55).mask(fav < 1.50, 4.00)
        return (fav >= 1.30) & (fav < 2.40) & (dr < thr)

    for lbl, seg in [("前半段", c[md <= md.median()]), ("后半段", c[md > md.median()])]:
        us = seg[_rule_mask(seg)]
        print(f"    {lbl} 规则命中 n={len(us)}  让方不胜={pct(us['fav_not_cover'].mean())}")

    # ---------- 规则内 让胜/让平/让负 拆分（用户要求：让平与让负分开） ----------
    line("9. 规则内 让胜 / 让平 / 让负 拆分")
    _neg = (~c["fav_cover"]) & (~c["hcp_draw"])

    def _split(s):
        return (pct(s["fav_cover"].mean()), pct(s["hcp_draw"].mean()),
                pct((((~s["fav_cover"]) & (~s["hcp_draw"])).mean())))

    print(f"  全样本 n={len(c)}  [让胜{_split(c)[0]} 让平{_split(c)[1]} 让负{_split(c)[2]}]")
    for code, name, lo, hi, dmax in RULES:
        band = c[(c["fav_odds"] >= lo) & (c["fav_odds"] < hi)]
        s = band[band["draw_odds"] < dmax]
        if len(s) == 0:
            continue
        sw, sd, sl = _split(s)
        bw, bd, bl = _split(band)
        print(f"  {code} n={len(s):>4} 规则[让胜{sw} 让平{sd} 让负{sl}]  "
              f"档base[让胜{bw} 让平{bd} 让负{bl}]")

    print("\n  draw_anom 五分位的三分拆（对照）:")
    for q in range(5):
        s = c[c["anom_q"] == q]
        sw, sd, sl = _split(s)
        print(f"    Q{q+1} n={len(s):>4}  让胜{sw} 让平{sd} 让负{sl}")

    print("\n  平赔相对该档 上下两侧（用档内平赔分位）:")
    for q in (0, 4):
        s = c[c["anom_q"] == q]
        sw, sd, sl = _split(s)
        print(f"    {'Q1 平赔最被顶高' if q == 0 else 'Q5 平赔最被拉低'} n={len(s):>4}  "
              f"让胜{sw} 让平{sd} 让负{sl}")

    # ---------- 每档「平赔低位 / 高位」的 让胜·让平·让负（定义让平/让负两族规则） ----------
    line("10. 每档 平赔低位(P33)/高位(P67) 的 让胜·让平·让负")
    print(f"  {'档':>13} {'组':>10} {'n':>5} {'让胜':>7} {'让平':>7} {'让负':>7}")
    for lo, hi in bands:
        bnd = c[(c["fav_odds"] >= lo) & (c["fav_odds"] < hi)]
        if len(bnd) < 30:
            continue
        p33 = bnd["draw_odds"].quantile(0.33)
        p67 = bnd["draw_odds"].quantile(0.67)
        for tag, seg in [("base", bnd),
                         ("低平赔", bnd[bnd["draw_odds"] <= p33]),
                         ("高平赔", bnd[bnd["draw_odds"] >= p67])]:
            sw, sd, sl = _split(seg)
            print(f"  [{lo:.2f},{hi:.2f}) {tag:>10} {len(seg):>5} "
                  f"{sw.replace('%',''):>7} {sd.replace('%',''):>7} {sl.replace('%',''):>7}")
        print()

    print("\n[完成]")


if __name__ == "__main__":
    main()
