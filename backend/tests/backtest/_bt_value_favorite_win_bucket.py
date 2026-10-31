"""价值分析 · 胜(正路胜) 按主队锐利盘胜赔分桶，逐场赛果明细（锐利盘口径）。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_fav_win_bucket.py [比赛日数]
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


def _o(v):
    return "—" if v is None else f"{v:.2f}"


BUCKETS = [
    ("<1.4", lambda o: o < 1.4),
    ("1.4~1.6", lambda o: 1.4 <= o < 1.6),
    ("1.6~1.8", lambda o: 1.6 <= o < 1.8),
    ("1.8~2.0", lambda o: 1.8 <= o < 2.0),
    ("2.0~2.3", lambda o: 2.0 <= o < 2.3),
    ("2.3~2.6", lambda o: 2.3 <= o < 2.6),
    (">=2.6", lambda o: o >= 2.6),
]


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
        hit = diff > 0
        line = _to_f(line_map.get(r["id"]))

        recs.append({
            "matchday": r["matchday"], "match_num": r["match_num"], "league": r["league"],
            "home": r["home_name"], "away": r["away_name"],
            "score": f"{r['home_score']}-{r['away_score']}",
            "sh_h": sharp.get("home"), "sh_d": sharp.get("draw"), "sh_a": sharp.get("away"),
            "line": line, "hit": hit,
        })

    recs.sort(key=lambda x: (x["sh_h"] if x["sh_h"] is not None else 99))
    print(f"胜(正路胜) 按主队锐利盘胜赔分桶逐场明细（锐利盘口径，近{LAST_MATCHDAYS}比赛日，共{len(recs)}场）")
    for name, cond in BUCKETS:
        sub = [x for x in recs if x["sh_h"] is not None and cond(x["sh_h"])]
        if not sub:
            continue
        hit = sum(1 for x in sub if x["hit"])
        print("\n" + "=" * 100)
        print(f"【主队锐利盘胜赔 {name}】 n={len(sub)}  胜命中(主队赢) {hit}/{len(sub)}={hit/len(sub)*100:.1f}%")
        print("-" * 100)
        for x in sub:
            mark = "✓" if x["hit"] else "✗"
            print(f"  {x['matchday']} {x['match_num'] or '':8s} {x['league'] or '':8s} {x['home']} vs {x['away']} [{x['score']}] {mark}")
            print(f"      锐利盘 主{_o(x['sh_h'])}/平{_o(x['sh_d'])}/客{_o(x['sh_a'])} · 让球线={_o(x['line'])}")


if __name__ == "__main__":
    asyncio.run(main())
