"""赔率组合规则挖掘 v2：只保留"有意义的选项"（双选两腿都 >= MIN_LEG，单选赔率 >= MIN_LEG）。

目的：排除"低赔填充腿（<1.4）"造成的高命中假象，找出两腿都有真实赔率的高命中组合。
运行：docker exec -w /app -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/combo2.py
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
MIN_LEG = 1.5


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
    c["loss_odds"] = np.where(c["fav"] == "home", c["hhad_away"], c["hhad_home"])  # 让负(让方未赢)
    c["cover_odds"] = np.where(c["fav"] == "home", c["hhad_home"], c["hhad_away"])  # 让胜

    c["act_h"] = (c["home_score"] > c["away_score"]).astype(float)
    c["act_d"] = (c["home_score"] == c["away_score"]).astype(float)
    c["act_a"] = (c["home_score"] < c["away_score"]).astype(float)
    adj = c["home_score"] + c["line"]
    hcp = np.select([adj > c["away_score"], adj < c["away_score"]], ["win", "loss"], default="push")
    c["hcp_push"] = (hcp == "push").astype(float)
    c["cover"] = np.where(c["fav"] == "home", (hcp == "win"), (hcp == "loss"))

    # 目标：(hit, implied, leg1_odds, leg2_odds)  leg=单选时 leg2 用 NaN
    nan = pd.Series(np.nan, index=c.index)
    T = {
        "双选·让方不胜(让平+让负)": (1 - c["cover"], 1 - c["q_cover"], c["hhad_draw"], c["loss_odds"]),
        "双选·让满(让胜+让平)": (c["cover"] + c["hcp_push"], c["q_cover"] + q_d, c["cover_odds"], c["hhad_draw"]),
        "双选·主队不败(主+平)": (c["act_h"] + c["act_d"], c["p_h"] + c["p_d"], c["had_home"], c["had_draw"]),
        "双选·客队不败(平+客)": (c["act_a"] + c["act_d"], c["p_a"] + c["p_d"], c["had_draw"], c["had_away"]),
        "双选·分出胜负(主+客)": (c["act_h"] + c["act_a"], c["p_h"] + c["p_a"], c["had_home"], c["had_away"]),
        "单选·主胜": (c["act_h"], c["p_h"], c["had_home"], nan),
        "单选·客胜": (c["act_a"], c["p_a"], c["had_away"], nan),
        "单选·平": (c["act_d"], c["p_d"], c["had_draw"], nan),
        "单选·让胜": (c["cover"], c["q_cover"], c["cover_odds"], nan),
        "单选·让平": (c["hcp_push"], q_d, c["hhad_draw"], nan),
    }
    F = {
        "fav": pd.cut(c["fav_odds"], [1.0, 1.4, 1.6, 1.8, 2.1, 2.5, 99], right=False).astype(str),
        "draw": pd.cut(c["had_draw"], [2.5, 3.0, 3.3, 3.6, 4.0, 99], right=False).astype(str),
        "dog": pd.cut(c["dog_odds"], [1.8, 3.0, 4.0, 5.5, 99], right=False).astype(str),
        "line": c["line"].map(lambda v: f"{v:+.0f}"),
        "side": c["fav"].astype(str),
    }
    return c, F, T


def run(c, F, T):
    combos = []
    for r in (2, 3):
        combos += [list(x) for x in combinations(F.keys(), r)]
    for tname, (hit, imp, o1, o2) in T.items():
        is_dual = o2.notna().any()
        rows = []
        for combo in combos:
            key = pd.Series([" | ".join(t) for t in zip(*[F[f] for f in combo])], index=c.index)
            for gval, idx in key.groupby(key).groups.items():
                if len(idx) < MIN_N:
                    continue
                # 逐场过滤：单选的选项、双选的两腿都必须 >= MIN_LEG
                if is_dual:
                    keep = (o1.loc[idx] >= MIN_LEG) & (o2.loc[idx] >= MIN_LEG)
                else:
                    keep = (o1.loc[idx] >= MIN_LEG)
                idx2 = idx[keep]
                if len(idx2) < MIN_N:
                    continue
                h = hit.loc[idx2]
                p = imp.loc[idx2]
                m1 = o1.loc[idx2].mean()
                m2 = o2.loc[idx2].mean() if is_dual else np.nan
                rows.append({"combo": " + ".join(combo), "cond": gval, "n": len(idx2),
                             "hit": h.mean(), "imp": p.mean(), "z": zz(h, p),
                             "o1": m1, "o2": m2, "low": m1 if not is_dual else min(m1, m2)})
        rows.sort(key=lambda r: (-r["hit"], -r["n"]))
        print(f"\n=== {tname}  全样本命中={pct(hit.mean())}  门槛: 最低腿赔率>={MIN_LEG} ===")
        if not rows:
            print("  无满足条件的组合（两腿都>=1.5 的高命中规则不存在）")
            continue
        print(f"  {'命中':>7} {'隐含':>7} {'差':>8} {'z':>6} {'n':>5} {'腿1赔':>6} {'腿2赔':>6}  条件")
        for r in rows[:6]:
            o2s = "-" if np.isnan(r["o2"]) else f"{r['o2']:>6.2f}"
            print(f"  {pct(r['hit'])} {pct(r['imp'])} {(r['hit']-r['imp'])*100:>+6.1f}pp {r['z']:>+6.2f} "
                  f"{r['n']:>5} {r['o1']:>6.2f} {o2s:>6}  [{r['combo']}] {r['cond']}")


async def main():
    c, F, T = await load()
    print(f"样本 {len(c)} 场（终盘）")
    run(c, F, T)


asyncio.run(main())
