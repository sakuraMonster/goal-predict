"""赔率规则扫描（探索性，只读）：在全部已结算比赛上按赔率带统计实际命中 vs 隐含概率 + ROI。

覆盖：
  A. 胜平负(主胜) 按主胜赔率带
  B. 胜平负(平局) 按平赔带
  C. 胜平负(客胜) 按客胜赔率带
  D. 让胜(fav 让球赢盘) 按 fav 胜赔带
  E. 让胜 按 HHAD 让胜赔率带
  F. 主/客让球方 不对称
显著性：z = (Σactual - Σp)/sqrt(Σp(1-p))（校准检验）；ROI = mean(命中×原始赔率)，>1 才正收益。
运行：docker exec -w /app -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/scan.py
"""
import os
import asyncio
from math import sqrt

import numpy as np
import pandas as pd
import asyncpg

SQL = """
SELECT m.id AS match_id,
       m.kickoff_time::date::text AS d,
       m.home_score, m.away_score,
       s.had_home, s.had_draw, s.had_away,
       s.hhad_line, s.hhad_home, s.hhad_draw, s.hhad_away,
       s.snapshot_time::text AS st
FROM matches m
JOIN jczq_play_odds_snapshots s ON s.match_id = m.id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND s.had_home IS NOT NULL AND s.had_draw IS NOT NULL AND s.had_away IS NOT NULL
ORDER BY m.id, s.snapshot_time
"""


def pct(x):
    return "  n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:5.1f}%"


def roi(x):
    return "  n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:5.3f}"


def zb(actual, p):
    a = np.asarray(actual, dtype=float)
    p = np.asarray(p, dtype=float)
    denom = sqrt(float((p * (1 - p)).sum()))
    return (a.sum() - p.sum()) / denom if denom > 0 else float("nan")


async def load():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()
    raw = pd.DataFrame([dict(r) for r in rows])
    raw = raw.sort_values(["match_id", "st"])
    c = raw.groupby("match_id").tail(1).copy()  # 终盘
    inv = 1 / c["had_home"] + 1 / c["had_draw"] + 1 / c["had_away"]
    c["p_h"] = 1 / c["had_home"] / inv
    c["p_d"] = 1 / c["had_draw"] / inv
    c["p_a"] = 1 / c["had_away"] / inv
    c["line"] = c["hhad_line"]
    c["fav"] = np.where(c["line"] < 0, "home", np.where(c["line"] > 0, "away", None))
    # HAD 实际
    c["act_h"] = (c["home_score"] > c["away_score"]).astype(float)
    c["act_d"] = (c["home_score"] == c["away_score"]).astype(float)
    c["act_a"] = (c["home_score"] < c["away_score"]).astype(float)
    # 让球（fav 视角）
    adj = c["home_score"] + c["line"]
    hcp_home = np.select([adj > c["away_score"], adj < c["away_score"]], ["win", "loss"], default="draw")
    c["fav_cover"] = np.where(c["fav"] == "home", hcp_home == "win", hcp_home == "loss").astype(float)
    # fav HAD 赔率 / 隐含 / HHAD 让胜赔率与隐含
    c["fav_odds"] = np.where(c["fav"] == "home", c["had_home"], c["had_away"])
    c["fav_ip"] = np.where(c["fav"] == "home", c["p_h"], c["p_a"])
    hh = c[["hhad_home", "hhad_draw", "hhad_away"]].astype(float)
    hh_inv = (1 / hh).sum(axis=1)
    c["cover_odds"] = np.where(c["fav"] == "home", c["hhad_home"], c["hhad_away"])
    c["cover_ip"] = np.where(c["fav"] == "home", 1 / c["hhad_home"], 1 / c["hhad_away"]) / hh_inv
    return c


def scan(df, col, edges, target_act, target_p, label):
    print(f"\n=== {label} ===")
    print(f"  {'赔率带':>14} {'n':>5} {'隐含':>7} {'实际':>7} {'差':>9} {'z':>6}")
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else None
        m = df[col] >= lo
        if hi is not None:
            m &= df[col] < hi
        s = df[m]
        if len(s) < 30:
            continue
        imp = s[target_p].mean()
        act = s[target_act].mean()
        z = zb(s[target_act], s[target_p])
        rng = f"[{lo:.2f},{(hi if hi else 99):.2f})"
        star = " *" if (abs(z) >= 2.5 and len(s) >= 150) else "  "
        print(f"  {rng:>14} {len(s):>5} {pct(imp)} {pct(act)} {(act-imp)*100:>+8.1f}pp {z:>+6.2f}{star}")


async def main():
    c = await load()
    print(f"样本：{len(c)} 场（终盘；含让球方 {int(c['fav'].notna().sum())} 场）")
    print("标记 * = |z|>=2.5 且 n>=150（但多桶扫描下仍可能是噪声，需 OOS 复核）")

    scan(c, "had_home", [1.20, 1.40, 1.60, 1.80, 2.00, 2.20, 2.50, 3.00, 3.50, 4.50, 6.0, 99],
         "act_h", "p_h", "A. 主胜：按主胜赔率带（实际命中率 vs 隐含概率）")
    scan(c, "had_draw", [2.80, 3.00, 3.20, 3.40, 3.60, 3.90, 4.20, 4.60, 5.20, 99],
         "act_d", "p_d", "B. 平局：按平赔带（实际命中率 vs 隐含概率）")
    scan(c, "had_away", [1.20, 1.40, 1.60, 1.80, 2.00, 2.20, 2.50, 3.00, 3.50, 4.50, 6.0, 99],
         "act_a", "p_a", "C. 客胜：按客胜赔率带（实际命中率 vs 隐含概率）")

    cc = c[c["fav"].notna()].copy()
    scan(cc, "fav_odds", [1.20, 1.40, 1.60, 1.80, 2.00, 2.20, 2.50, 3.00, 99],
         "fav_cover", "cover_ip", "D. 让胜：按让方(HAD)胜赔带（实际赢盘率 vs HHAD让胜隐含）")
    scan(cc, "cover_odds", [1.20, 1.40, 1.60, 1.80, 2.00, 2.30, 2.60, 3.00, 99],
         "fav_cover", "cover_ip", "E. 让胜：按 HHAD 让胜赔率带（实际赢盘率 vs 隐含）")

    print("\n=== F. 主/客让球方 不对称（让胜率） ===")
    for k in ("home", "away"):
        s = cc[cc["fav"] == k]
        print(f"  let方={k:<4} n={len(s):>5}  让胜率={pct(s['fav_cover'].mean())}  "
              f"让方不胜={pct(1 - s['fav_cover'].mean())}")

    print("\n=== G. 稳定性复核：把异动桶按时间对半拆（前后段一致才算真信号） ===")
    for name, col, lo, hi, act, p in [
        ("A 主胜[2.50,3.00)", "had_home", 2.50, 3.00, "act_h", "p_h"),
        ("A 主胜[4.50,6.00)", "had_home", 4.50, 6.00, "act_h", "p_h"),
        ("C 客胜[1.60,1.80)", "had_away", 1.60, 1.80, "act_a", "p_a"),
        ("B 平局[3.40,3.90)", "had_draw", 3.40, 3.90, "act_d", "p_d"),
    ]:
        sub = c[(c[col] >= lo) & (c[col] < hi)].copy()
        sub["_dt"] = pd.to_datetime(sub["d"])
        med = sub["_dt"].median()
        for tag, seg in [("前段", sub[sub["_dt"] <= med]), ("后段", sub[sub["_dt"] > med])]:
            if len(seg) < 30:
                continue
            print(f"  {name} {tag} n={len(seg):>4} 隐含={pct(seg[p].mean())} "
                  f"实际={pct(seg[act].mean())} 差={(seg[act].mean()-seg[p].mean())*100:+.1f}pp "
                  f"z={zb(seg[act], seg[p]):+.2f}")


asyncio.run(main())
