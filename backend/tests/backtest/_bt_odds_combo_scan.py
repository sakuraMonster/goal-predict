"""赔率组合规则挖掘（探索性，只读）：2~3 个赔率特征分桶组合 → 高命中双选/单选。

特征：fav_odds(让方HAD胜赔) / draw_odds(HAD平赔) / dog_odds(受让方HAD赔) / line(让球线) / side(让球方主客)
目标：让方不胜 / 让满(不败盘) / 主队不败 / 客队不败 / 分出胜负 / 主胜 / 让胜 / 平 / 客胜
门槛：n>=150；输出隐含基准、差(lift)、z、前后段命中率（判断稳定性）。
运行：docker exec -w /app -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/combo.py
"""
import os
import asyncio
from math import sqrt
from itertools import combinations

import numpy as np
import pandas as pd
import asyncpg

SQL = """
SELECT m.id AS match_id, m.kickoff_time::date::text AS d, m.home_score, m.away_score,
       s.had_home, s.had_draw, s.had_away,
       s.hhad_line, s.hhad_home, s.hhad_draw, s.hhad_away, s.snapshot_time::text AS st
FROM matches m
JOIN jczq_play_odds_snapshots s ON s.match_id = m.id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND s.had_home IS NOT NULL AND s.had_draw IS NOT NULL AND s.had_away IS NOT NULL
ORDER BY m.id, s.snapshot_time
"""

MIN_N = 150


def pct(x):
    return "  n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:5.1f}%"


def zz(hit, imp):
    h = np.asarray(hit, float); p = np.asarray(imp, float)
    d = sqrt(float((p * (1 - p)).sum()))
    return (h.sum() - p.sum()) / d if d > 0 else float("nan")


async def load():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()
    raw = pd.DataFrame([dict(r) for r in rows]).sort_values(["match_id", "st"])
    c = raw.groupby("match_id").tail(1).copy()

    inv = 1 / c["had_home"] + 1 / c["had_draw"] + 1 / c["had_away"]
    c["p_h"], c["p_d"], c["p_a"] = 1 / c["had_home"] / inv, 1 / c["had_draw"] / inv, 1 / c["had_away"] / inv
    c["line"] = c["hhad_line"]
    c["fav"] = np.where(c["line"] < 0, "home", np.where(c["line"] > 0, "away", None))
    c = c[c["fav"].notna()].reset_index(drop=True)
    hh_inv = 1 / c["hhad_home"] + 1 / c["hhad_draw"] + 1 / c["hhad_away"]
    q_h, q_d, q_a = 1 / c["hhad_home"] / hh_inv, 1 / c["hhad_draw"] / hh_inv, 1 / c["hhad_away"] / hh_inv
    c["q_cover"] = np.where(c["fav"] == "home", q_h, q_a)

    c["fav_odds"] = np.where(c["fav"] == "home", c["had_home"], c["had_away"])
    c["dog_odds"] = np.where(c["fav"] == "home", c["had_away"], c["had_home"])

    c["act_h"] = (c["home_score"] > c["away_score"]).astype(float)
    c["act_d"] = (c["home_score"] == c["away_score"]).astype(float)
    c["act_a"] = (c["home_score"] < c["away_score"]).astype(float)

    adj = c["home_score"] + c["line"]
    hcp = np.select([adj > c["away_score"], adj < c["away_score"]], ["win", "loss"], default="push")
    c["hcp_win"] = (hcp == "win").astype(float)
    c["hcp_push"] = (hcp == "push").astype(float)
    c["hcp_loss"] = (hcp == "loss").astype(float)
    c["cover"] = np.where(c["fav"] == "home", c["hcp_win"], c["hcp_loss"])
    c["cover_no_lose"] = c["cover"] + (c["hcp_push"])  # 让胜或让平

    # 目标：(hit, implied)
    T = {
        "让方不胜(让平+让负)": (1 - c["cover"], 1 - c["q_cover"]),
        "让满(让胜+让平)": (c["cover_no_lose"], c["q_cover"] + q_d),
        "主队不败(主+平)": (c["act_h"] + c["act_d"], c["p_h"] + c["p_d"]),
        "客队不败(平+客)": (c["act_a"] + c["act_d"], c["p_a"] + c["p_d"]),
        "分出胜负(主+客)": (c["act_h"] + c["act_a"], c["p_h"] + c["p_a"]),
        "单选·主胜": (c["act_h"], c["p_h"]),
        "单选·客胜": (c["act_a"], c["p_a"]),
        "单选·平": (c["act_d"], c["p_d"]),
        "单选·让胜": (c["cover"], c["q_cover"]),
        "单选·让平": (c["hcp_push"], q_d),
    }
    # 特征分桶
    F = {
        "fav": pd.cut(c["fav_odds"], [1.0, 1.4, 1.6, 1.8, 2.1, 2.5, 99], right=False).astype(str),
        "draw": pd.cut(c["had_draw"], [2.5, 3.0, 3.3, 3.6, 4.0, 99], right=False).astype(str),
        "dog": pd.cut(c["dog_odds"], [1.8, 3.0, 4.0, 5.5, 99], right=False).astype(str),
        "line": c["line"].map(lambda v: f"{v:+.0f}"),
        "side": c["fav"].astype(str),
    }
    c["_dt"] = pd.to_datetime(c["d"])
    return c, F, T


def run(c, F, T):
    feat_names = list(F.keys())
    combos = []
    for r in (2, 3):
        combos += [list(x) for x in combinations(feat_names, r)]
    for tname, (hit, imp) in T.items():
        rows = []
        base_hit = hit.mean()
        for combo in combos:
            keys = list(zip(*[F[f] for f in combo]))
            key = pd.Series([" | ".join(t) for t in keys], index=c.index)
            for gval, idx in key.groupby(key).groups.items():
                n = len(idx)
                if n < MIN_N:
                    continue
                h = hit.loc[idx]
                p = imp.loc[idx]
                if hit.loc[idx].isna().any():
                    continue
                seg = c.loc[idx]
                med = seg["_dt"].median()
                h1 = hit.loc[seg[seg["_dt"] <= med].index].mean()
                h2 = hit.loc[seg[seg["_dt"] > med].index].mean()
                rows.append({
                    "combo": " + ".join(combo), "cond": gval, "n": n,
                    "hit": h.mean(), "imp": p.mean(), "z": zz(h, p),
                    "h1": h1, "h2": h2,
                })
        rows.sort(key=lambda r: (-r["hit"], -r["n"]))
        print(f"\n=== 目标：{tname}  全样本命中={pct(base_hit)} ===")
        print(f"  {'命中':>7} {'隐含':>7} {'差':>8} {'z':>6} {'n':>5} {'前段':>7} {'后段':>7}  条件")
        shown = 0
        for r in rows:
            if shown >= 6:
                break
            shown += 1
            print(f"  {pct(r['hit'])} {pct(r['imp'])} {(r['hit']-r['imp'])*100:>+6.1f}pp "
                  f"{r['z']:>+6.2f} {r['n']:>5} {pct(r['h1'])} {pct(r['h2'])}  "
                  f"[{r['combo']}] {r['cond']}")


async def main():
    c, F, T = await load()
    print(f"样本 {len(c)} 场（终盘，全部含让球方）")
    print("说明：差=实际命中-隐含；前/后段=按时间中位对半的命中率（两者都高才算稳）")
    run(c, F, T)


asyncio.run(main())
