"""价值分析 · 胜(正路胜)失败场次分析（锐利盘口径）。

胜(正路胜) = 价值分析预测主胜 + 主队为让球方 → 提取 HAD 胜。
失败 = 主队没赢（主队打平或客胜）。
按 主队(让球方)锐利盘胜赔/客队/平赔 分桶，看哪些区间「胜」容易失败（主队不赢）。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_fav_win_ana.py [比赛日数]
"""
import sys
import os
import asyncio
import json
from collections import defaultdict

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30

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
        sharp = m1.get("sharp_odds") or {}
        handicap = (a.get("markets") or {}).get("handicap") or {}
        has_hhad = bool(handicap.get("jczq_odds"))
        if best_side != "home" or not has_hhad:
            continue
        fav = _fav_from_1x2(sharp)
        if fav != "home":
            continue

        diff = r["home_score"] - r["away_score"]
        hit = diff > 0  # 主队赢 = 胜命中

        fav_odds = _to_f(sharp.get("home"))
        dog_odds = _to_f(sharp.get("away"))
        draw_odds = _to_f(sharp.get("draw"))
        recs.append({
            "match_num": r["match_num"], "home": r["home_name"], "away": r["away_name"],
            "score": f"{r['home_score']}-{r['away_score']}",
            "fav_odds": fav_odds, "dog_odds": dog_odds, "draw_odds": draw_odds,
            "hit": hit,
        })

    n = len(recs)
    hit = sum(1 for x in recs if x["hit"])
    fail = sum(1 for x in recs if not x["hit"])
    print(f"胜(正路胜) n={n}，命中 {hit}={hit/n*100 if n else 0:.1f}%，失败 {fail}={fail/n*100 if n else 0:.1f}%（锐利盘口径，近{LAST_MATCHDAYS}比赛日）")
    print("=" * 90)

    def agg(keyfn, name):
        g = defaultdict(lambda: [0, 0])
        for x in recs:
            k = keyfn(x)
            g[k][0] += 1
            if x["hit"]:
                g[k][1] += 1
        print(f"\n按 {name} 分桶（胜命中率=主队赢）：")
        for k in sorted(g, key=lambda k: str(k)):
            c = g[k]
            print(f"  {str(k):14s} n={c[0]:3d}  命中 {_p(c[1], c[0])}")

    def fv(x):
        o = x["fav_odds"] or 0
        return "<1.4" if o < 1.4 else ("1.4~1.6" if o < 1.6 else ("1.6~1.8" if o < 1.8 else ("1.8~2.0" if o < 2.0 else ("2.0~2.3" if o < 2.3 else ("2.3~2.6" if o < 2.6 else ">=2.6")))))

    def dv(x):
        o = x["dog_odds"] or 0
        return "<3.0" if o < 3.0 else ("3.0~4.0" if o < 4.0 else ("4.0~5.0" if o < 5.0 else (">=5.0")))

    def dr(x):
        o = x["draw_odds"] or 0
        return "<3.5" if o < 3.5 else ("3.5~4.0" if o < 4.0 else (">=4.0"))

    agg(fv, "主队(让球方)锐利盘胜赔")
    agg(dv, "客队(受让方)锐利盘胜赔")
    agg(dr, "锐利盘平赔")

    # 失败场次（主队没赢）汇总
    fails = [x for x in recs if not x["hit"]]
    print(f"\n失败场次 {len(fails)} 场（主队打平或客胜），主队锐利盘胜赔分布：")
    buckets = defaultdict(int)
    for x in fails:
        buckets[fv(x)] += 1
    for k in ["<1.4", "1.4~1.6", "1.6~1.8", "1.8~2.0", "2.0~2.3", "2.3~2.6", ">=2.6"]:
        if buckets.get(k):
            print(f"  {k}: {buckets[k]} 场")


if __name__ == "__main__":
    asyncio.run(main())
