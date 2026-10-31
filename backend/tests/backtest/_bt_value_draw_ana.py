"""价值分析 · 「平+让平」(均衡盘平) 失败场次分析（锐利盘口径）。

失败 = 主队赢盘(让胜) 或 主队输盘(让负)，即分出胜负。
按 让球方主客 / 让球方锐利盘胜赔 / 平赔 / 让球线 分桶，看哪些区间「平+让平」容易失败。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_draw_ana.py [比赛日数]
"""
import sys
import os
import asyncio
import json
from collections import defaultdict

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
DEEP_FAV_ODDS = 1.50
DECISIVE_LO = 1.80
DECISIVE_HI = 2.00

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
        if best_side != "draw" or not has_hhad:
            continue
        fav = _fav_from_1x2(sharp)
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        if not (fav_odds is None or fav_odds >= DEEP_FAV_ODDS):
            continue
        if fav is not None and fav_odds is not None and DECISIVE_LO <= fav_odds < DECISIVE_HI:
            continue

        diff = r["home_score"] - r["away_score"]
        line = _to_f(line_map.get(r["id"]))
        hhad = None
        if line is not None:
            adj = diff + line
            hhad = "home" if adj > 0 else ("draw" if adj == 0 else "away")
        hit = (diff == 0) or (hhad == "draw")

        draw_odds = _to_f(sharp.get("draw"))
        recs.append({
            "match_num": r["match_num"], "home": r["home_name"], "away": r["away_name"],
            "score": f"{r['home_score']}-{r['away_score']}",
            "fav": fav, "fav_odds": fav_odds, "draw_odds": draw_odds, "line": line,
            "hit": hit,
        })

    n = len(recs)
    hit = sum(1 for x in recs if x["hit"])
    fail = sum(1 for x in recs if not x["hit"])
    print(f"平+让平(均衡盘平) n={n}，命中 {hit}={hit/n*100 if n else 0:.1f}%，失败 {fail}={fail/n*100 if n else 0:.1f}%（近{LAST_MATCHDAYS}比赛日）")
    print("=" * 90)

    def agg(keyfn, name):
        g = defaultdict(lambda: [0, 0])
        for x in recs:
            k = keyfn(x)
            g[k][0] += 1
            if x["hit"]:
                g[k][1] += 1
        print(f"\n按 {name} 分桶（平+让平命中率）：")
        for k in sorted(g, key=lambda k: str(k)):
            c = g[k]
            print(f"  {str(k):14s} n={c[0]:3d}  命中 {_p(c[1], c[0])}")

    agg(lambda x: "主队让球方" if x["fav"] == "home" else ("客队让球方" if x["fav"] == "away" else "无"), "让球方主客")

    def fv(x):
        o = x["fav_odds"]
        if o is None:
            return "无"
        if o < 1.6:
            return "1.5~1.6"
        if o < 1.8:
            return "1.6~1.8"
        if o < 2.0:
            return "1.8~2.0"
        if o < 2.3:
            return "2.0~2.3"
        if o < 2.6:
            return "2.3~2.6"
        return ">=2.6"

    agg(fv, "让球方锐利盘胜赔")

    def dr(x):
        o = x["draw_odds"] or 0
        return "<3.2" if o < 3.2 else ("3.2~3.6" if o < 3.6 else ("3.6~4.0" if o < 4.0 else ">=4.0"))

    agg(dr, "锐利盘平赔")

    def ln(x):
        l = x["line"]
        if l is None:
            return "无线"
        return "主让(-1)" if l < 0 else ("客让(+1)" if l > 0 else "平手(0)")

    agg(ln, "让球线")

    # 失败场次分布
    fails = [x for x in recs if not x["hit"]]
    print(f"\n失败场次 {len(fails)} 场，按让球方主客 + 让球方胜赔分布：")
    g = defaultdict(int)
    for x in fails:
        g[(("主让" if x["fav"] == "home" else "客让" if x["fav"] == "away" else "无"), fv(x))] += 1
    for k in sorted(g, key=lambda k: str(k)):
        print(f"  {k[0]}·fav{k[1]}: {g[k]} 场")


if __name__ == "__main__":
    asyncio.run(main())
