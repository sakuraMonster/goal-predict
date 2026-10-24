"""价值分析历史命中率回测 —— 按比赛日统计「有价值」1X2 方向命中 + OU 方向命中。

口径与生产 /api/value-analysis 完全一致：
  - 1X2 价值：竞彩 HAD 赔率 vs 锐利盘 1X2（Pinnacle 优先、bet365 兜底）去水后的 EV；
    verdict="有价值" 当 best_ev >= +2%。
  - 大小球价值：竞彩 TTG 隐含 P大 vs SportMonks O/U（2.5 线 Pinnacle 优先中位数）的错价方向。
  - 每场取「最新」快照（与生产一致），数据为已结算场次（home_score/away_score 非空）。

命中定义：
  - 1X2：best_side（最大 EV 方向）== 实际胜平负。
  - OU：cheap_side（"小球"→总进球≤2；"大球"→总进球≥3）== 实际大小球方向。

校准诊断：价值命中率 vs 价值平均真实概率（fair_prob）的差；负偏差 = 价值端被高估（冷门假价值）。

运行：docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va.py
"""
import sys
import os
import asyncio
import json

sys.path.insert(0, "/app")

import asyncpg
import pandas as pd
import numpy as np

from app.predictor.models.value_analysis import analyze_match
from app.predictor.models.ou_market import ou_market_from_rows

VALUE_VERDICT = "有价值"

MATCHES_SQL = """
SELECT m.id, m.match_num, m.kickoff_time, m.home_score, m.away_score,
       l.name_zh AS league, ht.name_zh AS home_name, at.name_zh AS away_name,
       ((m.kickoff_time - interval '12 hours')::date)::text AS matchday
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN teams ht ON ht.id = m.home_team_id
LEFT JOIN teams at ON at.id = m.away_team_id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
ORDER BY m.kickoff_time
"""

JCZQ_SQL = """
SELECT DISTINCT ON (match_id) match_id, had_home, had_draw, had_away, ttg_odds_json
FROM jczq_play_odds_snapshots
WHERE match_id = ANY($1) AND source = 'sporttery'
ORDER BY match_id, snapshot_time DESC
"""

SHARP_SQL = """
SELECT DISTINCT ON (match_id) match_id, bookmaker, home_win, draw, away_win
FROM odds_snapshots
WHERE match_id = ANY($1) AND bookmaker IN ('Pinnacle','bet365')
  AND home_win IS NOT NULL AND draw IS NOT NULL AND away_win IS NOT NULL
ORDER BY match_id, (bookmaker = 'Pinnacle') DESC, snapshot_time DESC
"""

OU_SQL = """
SELECT DISTINCT ON (match_id, bookmaker, goal_line) match_id, bookmaker, goal_line, over_odds, under_odds
FROM odds_snapshots
WHERE match_id = ANY($1) AND goal_line IS NOT NULL AND over_odds IS NOT NULL AND under_odds IS NOT NULL
ORDER BY match_id, bookmaker, goal_line, snapshot_time DESC
"""


def _f(v):
    return None if v is None else float(v)


def _pct(n, d):
    return float("nan") if not d else n / d


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)

    mrows = await conn.fetch(MATCHES_SQL)
    mids = [r["id"] for r in mrows]

    jczq_rows = await conn.fetch(JCZQ_SQL, mids)
    sharp_rows = await conn.fetch(SHARP_SQL, mids)
    ou_rows = await conn.fetch(OU_SQL, mids)
    await conn.close()

    jczq_map = {r["match_id"]: r for r in jczq_rows}
    sharp_map = {r["match_id"]: r for r in sharp_rows}
    ou_map = {}
    for r in ou_rows:
        ou_map.setdefault(r["match_id"], []).append(
            (r["bookmaker"], r["goal_line"], r["over_odds"], r["under_odds"])
        )

    records = []
    n_jczq = 0
    n_sharp = 0
    for m in mrows:
        mid = m["id"]
        hs, aw = m["home_score"], m["away_score"]
        if hs is None or aw is None:
            continue
        actual = "home" if hs > aw else ("draw" if hs == aw else "away")
        actual_over = (hs + aw) > 2.5

        j = jczq_map.get(mid)
        if j is None:
            continue
        had = {"home": _f(j["had_home"]), "draw": _f(j["had_draw"]), "away": _f(j["had_away"])}
        if not all(v is not None and v > 1.0 for v in had.values()):
            continue
        n_jczq += 1
        ttg_raw = j["ttg_odds_json"]
        if isinstance(ttg_raw, str):
            try:
                ttg = json.loads(ttg_raw)
            except Exception:
                ttg = None
        else:
            ttg = ttg_raw if isinstance(ttg_raw, dict) else None
        jczq = {"had": had, "ttg": ttg}

        s = sharp_map.get(mid)
        sharp_1x2 = {"home": _f(s["home_win"]), "draw": _f(s["draw"]), "away": _f(s["away_win"])} if s else {}
        if sharp_1x2:
            n_sharp += 1

        ou = ou_market_from_rows(ou_map.get(mid) or [])
        ou_p_big = ou["p_big"] if ou else None
        ou_n_books = ou["n_books"] if ou else None

        a = analyze_match(
            jczq=jczq,
            sharp_1x2=sharp_1x2,
            sharp_ou_p_big=ou_p_big,
            sharp_ou_n_books=ou_n_books,
        )

        v1 = a["markets"].get("1x2", {}).get("value", {})
        best_side = v1.get("best_side")
        verdict = v1.get("verdict")
        best_ev = v1.get("best_ev")
        outcomes = v1.get("outcomes") or {}
        best_fp = outcomes.get(best_side, {}).get("fair_prob") if best_side else None

        ou_v = a["markets"].get("ou", {}).get("value", {})
        ou_cheap = ou_v.get("cheap_side")
        ou_verdict = ou_v.get("verdict")
        if ou_cheap == "小球":
            ou_hit = not actual_over
        elif ou_cheap == "大球":
            ou_hit = actual_over
        else:
            ou_hit = None

        records.append({
            "matchday": m["matchday"],
            "match_num": m["match_num"],
            "league": m["league"],
            "home": m["home_name"] or m["match_num"],
            "away": m["away_name"] or "",
            "actual": actual,
            "verdict": verdict,
            "best_side": best_side,
            "best_ev": best_ev,
            "best_fp": best_fp,
            "hit_1x2": (best_side == actual) if best_side else None,
            "ou_verdict": ou_verdict,
            "ou_cheap": ou_cheap,
            "ou_hit": ou_hit,
        })

    df = pd.DataFrame(records)
    if df.empty:
        print("no records")
        return

    has_sharp = df["best_side"].notna()
    df_s = df[has_sharp].copy()  # 有锐利盘 1X2 基准 → 可做 1X2 方向判定
    is_value = df_s["verdict"] == VALUE_VERDICT
    df_v = df_s[is_value]

    def _agg(sub):
        n = len(sub)
        return {
            "n": n,
            "hit": int(sub["hit_1x2"].sum()) if n else 0,
            "rate": _pct(int(sub["hit_1x2"].sum()), n),
            "avg_fp": float(sub["best_fp"].mean()) if n else float("nan"),
        }

    # 整体
    all_agg = _agg(df_s)
    val_agg = _agg(df_v)

    print("=" * 100)
    print("价值分析历史命中率回测  (口径=生产 /api/value-analysis)")
    print("=" * 100)
    print(f"已结算场次(有比分)          : {len(df)}")
    print(f"有竞彩 HAD 盘口             : {n_jczq}")
    print(f"有锐利盘 1X2 基准(Pin/b365) : {n_sharp}  ({n_sharp / n_jczq * 100:.1f}% of HAD)")
    print(f"比赛日范围                  : {df_s['matchday'].min()} ~ {df_s['matchday'].max()}")
    print("-" * 100)
    print(f"[1X2 方向] 全样本(有基准) n={all_agg['n']}  方向命中 {all_agg['rate']*100:.1f}%")
    print(f"[1X2 价值] 有价值信号 n={val_agg['n']}  命中 {val_agg['rate']*100:.1f}%  "
          f"平均真实概率 {val_agg['avg_fp']*100:.1f}%  "
          f"偏差 { (val_agg['rate'] - val_agg['avg_fp']) * 100:+.1f}pp")

    # OU 汇总
    ou_sig = df_s[df_s["ou_cheap"].notna()]
    ou_hits = int(ou_sig["ou_hit"].sum()) if len(ou_sig) else 0
    print(f"[大小球方向] 错价信号(大球偏贵/小球偏贵) n={len(ou_sig)}  方向命中 {_pct(ou_hits, len(ou_sig))*100:.1f}%")

    # 按 verdict 分桶
    print("\n[1X2 方向命中率 按 verdict 分桶]")
    for v in ["有价值", "中性", "无价值"]:
        sub = df_s[df_s["verdict"] == v]
        if len(sub) == 0:
            print(f"  {v:8s}  n=0")
            continue
        h = int(sub["hit_1x2"].sum())
        print(f"  {v:8s}  n={len(sub):5d}  hit={h:4d}  rate={_pct(h, len(sub))*100:5.1f}%  "
              f"avg_fp={sub['best_fp'].mean()*100:5.1f}%  avg_ev={sub['best_ev'].mean()*100:+5.1f}%")

    # 每日表
    print("\n" + "=" * 100)
    print("每日明细")
    print("=" * 100)
    print(f"{'matchday':>12} {'总':>4} {'价值':>4} {'价中':>4} {'价命中率':>8} {'价avg_fp':>9} {'偏差':>7} | {'全方向命中率':>10}")
    daily = []
    for md, g in df_s.groupby("matchday", sort=True):
        n = len(g)
        gv = g[g["verdict"] == VALUE_VERDICT]
        nv = len(gv)
        vh = int(gv["hit_1x2"].sum()) if nv else 0
        vfp = float(gv["best_fp"].mean()) if nv else float("nan")
        ah = int(g["hit_1x2"].sum())
        vrate = _pct(vh, nv)
        bias = (vrate - vfp) if nv else float("nan")
        daily.append((md, n, nv, vh, vrate, vfp, bias, _pct(ah, n)))
        print(f"{md:>12} {n:4d} {nv:4d} {vh:4d} {vrate*100:7.1f}% {vfp*100:8.1f}% "
              f"{bias*100:+6.1f}pp | {_pct(ah, n)*100:9.1f}%")

    # 输出 JSON 供报告使用
    daily_json = [
        {"matchday": d[0], "n": d[1], "value": d[2], "value_hit": d[3],
         "value_rate": None if (d[4] != d[4]) else round(d[4], 4),
         "value_avg_fp": None if (d[5] != d[5]) else round(d[5], 4),
         "bias": None if (d[6] != d[6]) else round(d[6], 4),
         "all_rate": None if (d[7] != d[7]) else round(d[7], 4)}
        for d in daily
    ]
    print("\n__JSON_START__")
    print(json.dumps({
        "range": [df_s["matchday"].min(), df_s["matchday"].max()],
        "settled": len(df),
        "with_had": n_jczq,
        "with_sharp": n_sharp,
        "all_n": all_agg["n"], "all_rate": round(all_agg["rate"], 4),
        "value_n": val_agg["n"], "value_rate": round(val_agg["rate"], 4),
        "value_avg_fp": round(val_agg["avg_fp"], 4),
        "ou_n": len(ou_sig), "ou_rate": round(_pct(ou_hits, len(ou_sig)), 4),
        "daily": daily_json,
    }, ensure_ascii=False))
    print("__JSON_END__")


if __name__ == "__main__":
    asyncio.run(main())
