"""价值分析 · 均衡盘「平+让平」分支的细分探索（锐利盘口径）。

目的：对均衡盘平预测（fav_odds >= T 或无让球方）这批样本，按让球线/平赔/让方胜赔等特征分桶，
      看能否找到一个 2 分法把命中率明显拉开，从而把均衡盘再拆成两个子分支。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_balanced_split.py [比赛日数] [阈值T]
"""
import sys
import os
import asyncio
import json
from collections import defaultdict

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
T = float(sys.argv[2]) if len(sys.argv) > 2 else 1.50

MATCH_SQL = """
SELECT m.id, m.match_num, m.kickoff_time, m.home_score, m.away_score,
       l.name_zh AS league, ht.name_zh AS home_name, at.name_zh AS away_name,
       ((m.kickoff_time - interval '12 hours')::date)::text AS matchday
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN teams ht ON ht.id = m.home_team_id
LEFT JOIN teams at ON at.id = m.away_team_id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND m.kickoff_time >= now() - interval '120 days'
ORDER BY m.kickoff_time DESC
"""

SNAP_SQL = "SELECT match_id, analysis_json FROM value_analysis_snapshots WHERE match_id = ANY($1)"
LINE_SQL = """
SELECT DISTINCT ON (match_id) match_id, hhad_line
FROM jczq_play_odds_snapshots
WHERE match_id = ANY($1) ORDER BY match_id, snapshot_time DESC
"""


def _to_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fav_from_1x2(o):
    if not isinstance(o, dict):
        return None
    h = _to_f(o.get("home"))
    a = _to_f(o.get("away"))
    if h is None or a is None or h <= 0 or a <= 0:
        return None
    if h < a:
        return "home"
    if a < h:
        return "away"
    return None


def _load_json(v):
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def _p(n, d):
    return f"{n}/{d}={n/d*100:.1f}%" if d else "—"


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    mrows = await conn.fetch(MATCH_SQL)
    mids = [r["id"] for r in mrows]
    snap_map, line_map = {}, {}
    if mids:
        for r in await conn.fetch(SNAP_SQL, mids):
            snap_map[r["match_id"]] = _load_json(r["analysis_json"])
        for r in await conn.fetch(LINE_SQL, mids):
            line_map[r["match_id"]] = r["hhad_line"]
    await conn.close()

    seen = []
    for r in mrows:
        if r["matchday"] not in seen:
            seen.append(r["matchday"])
        if len(seen) >= LAST_MATCHDAYS:
            break
    target = set(seen)

    recs = []
    for r in mrows:
        if r["matchday"] not in target:
            continue
        a = snap_map.get(r["id"])
        if not isinstance(a, dict):
            continue
        m1 = (a.get("markets") or {}).get("1x2") or {}
        best_side = (m1.get("value") or {}).get("best_side")
        jczq = m1.get("jczq_odds") or {}
        sharp = m1.get("sharp_odds") or {}
        handicap = (a.get("markets") or {}).get("handicap") or {}
        has_hhad = bool(handicap.get("jczq_odds"))
        if best_side != "draw" or not has_hhad:
            continue
        fav = _fav_from_1x2(sharp)
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        if not (fav_odds is None or fav_odds >= T):
            continue  # 只保留均衡盘分支

        diff = r["home_score"] - r["away_score"]
        line = _to_f(line_map.get(r["id"]))
        had_actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        hhad_actual = None
        if line is not None:
            adj = diff + line
            hhad_actual = "home" if adj > 0 else ("draw" if adj == 0 else "away")
        ping_hit = had_actual == "draw"
        rangping_hit = (hhad_actual == "draw") if hhad_actual is not None else None
        any_hit = ping_hit or (rangping_hit is True)

        # 深盘分支(让球方负 + 让球方平)的命中：让球方负 = hhad 让球方输盘，让球方平 = hhad draw
        if fav == "home":
            deep_hit = (hhad_actual in ("draw", "away")) if hhad_actual is not None else None
        elif fav == "away":
            deep_hit = (hhad_actual in ("draw", "home")) if hhad_actual is not None else None
        else:
            deep_hit = None

        dog = "away" if fav == "home" else ("home" if fav == "away" else None)
        dog_odds = _to_f(sharp.get(dog)) if dog else None
        recs.append({
            "matchday": r["matchday"], "match_num": r["match_num"],
            "home": r["home_name"], "away": r["away_name"],
            "score": f"{r['home_score']}-{r['away_score']}", "diff": diff,
            "line": line, "fav": fav, "fav_odds": fav_odds, "dog_odds": dog_odds,
            "sharp_draw": _to_f(sharp.get("draw")), "jczq_draw": _to_f(jczq.get("draw")),
            "ping": ping_hit, "rangping": rangping_hit, "any": any_hit, "deep": deep_hit,
        })

    n = len(recs)
    any_hit = sum(1 for x in recs if x["any"])
    print(f"均衡盘「平+让平」样本 n={n}，任一命中 {any_hit}/{n}={any_hit/n*100:.1f}%（锐利盘口径 T={T}，近{LAST_MATCHDAYS}比赛日）")
    print("=" * 90)

    def agg(keyfn, name):
        g = defaultdict(lambda: [0, 0])
        for x in recs:
            k = keyfn(x)
            g[k][0] += 1
            if x["any"]:
                g[k][1] += 1
        print(f"\n按 {name} 分桶：")
        for k in sorted(g, key=lambda k: str(k)):
            c = g[k]
            print(f"  {str(k):14s} {_p(c[1], c[0])}")

    agg(lambda x: ("主让" if (x["line"] is not None and x["line"] < 0) else ("客让" if (x["line"] is not None and x["line"] > 0) else ("平手" if x["line"] == 0 else "无线"))), "让球线方向")
    agg(lambda x: "主队让球方" if x["fav"] == "home" else ("客队让球方" if x["fav"] == "away" else "无"), "让球方主客")
    agg(lambda x: f"fav<1.75" if (x["fav_odds"] or 0) < 1.75 else (f"fav1.75~2.0" if (x["fav_odds"] or 0) < 2.0 else (f"fav2.0~2.5" if (x["fav_odds"] or 0) < 2.5 else "fav>=2.5")), "锐利盘让方胜赔")
    agg(lambda x: f"平<3.0" if (x["sharp_draw"] or 0) < 3.0 else (f"平3.0~3.4" if (x["sharp_draw"] or 0) < 3.4 else (f"平3.4~3.8" if (x["sharp_draw"] or 0) < 3.8 else "平>=3.8")), "锐利盘平赔")
    agg(lambda x: f"竞彩平<3.0" if (x["jczq_draw"] or 0) < 3.0 else (f"竞彩平3.0~3.2" if (x["jczq_draw"] or 0) < 3.2 else (f"竞彩平3.2~3.5" if (x["jczq_draw"] or 0) < 3.5 else "竞彩平>=3.5")), "竞彩平赔")

    # 主让/客让 两条分支命中率对比
    print("\n主让/客让 × 两条分支命中率对比：")
    for side, label in [("home", "主让"), ("away", "客让")]:
        sub = [x for x in recs if x["fav"] == side]
        if not sub:
            continue
        n2 = len(sub)
        bal = sum(1 for x in sub if x["any"])
        dep = sum(1 for x in sub if x["deep"])
        print(f"  {label}(n={n2}): 平+让平 {_p(bal, n2)} | 深盘(让球方负+让球方平) {_p(dep, n2)}")

    # 阈值扫描：锐利盘平赔 / 让方胜赔 找最优 2 分
    def sweep(key, lo, hi, step, label):
        best = None
        for t in [round(lo + step * i, 2) for i in range(int((hi - lo) / step) + 1)]:
            lo_n = lo_h = hi_n = hi_h = 0
            for x in recs:
                v = x[key] or 0
                if v < t:
                    lo_n += 1
                    if x["any"]:
                        lo_h += 1
                else:
                    hi_n += 1
                    if x["any"]:
                        hi_h += 1
            if lo_n and hi_n:
                gap = abs(lo_h / lo_n - hi_h / hi_n)
                if best is None or gap > best[0]:
                    best = (gap, t, lo_n, lo_h, hi_n, hi_h)
        if best:
            _, t, lo_n, lo_h, hi_n, hi_h = best
            print(f"\n{label} 最优2分阈值≈{t}： <{t} 命中 {_p(lo_h, lo_n)} | >={t} 命中 {_p(hi_h, hi_n)}")

    sweep("sharp_draw", 2.6, 4.4, 0.05, "锐利盘平赔")
    sweep("fav_odds", 1.5, 3.0, 0.05, "锐利盘让方胜赔")
    sweep("jczq_draw", 2.6, 4.0, 0.05, "竞彩平赔")


if __name__ == "__main__":
    asyncio.run(main())
