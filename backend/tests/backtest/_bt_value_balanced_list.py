"""价值分析 · 均衡盘「平+让平」场次单独列出（锐利盘口径）。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_balanced_list.py [比赛日数] [阈值T]
"""
import sys
import os
import asyncio
import json

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
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


def _o(v):
    return "—" if v is None else f"{v:.2f}"


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

    rows = []
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
        # 均衡盘：让球方胜赔 >= T 或无让球方
        if not (fav_odds is None or fav_odds >= T):
            continue

        diff = r["home_score"] - r["away_score"]
        line = _to_f(line_map.get(r["id"]))
        hhad_actual = None
        if line is not None:
            adj = diff + line
            hhad_actual = "home" if adj > 0 else ("draw" if adj == 0 else "away")
        had_actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        ping = had_actual == "draw"
        rangping = (hhad_actual == "draw") if hhad_actual is not None else None
        any_hit = ping or (rangping is True)

        rows.append({
            "matchday": r["matchday"], "match_num": r["match_num"], "league": r["league"],
            "home": r["home_name"] or r["match_num"], "away": r["away_name"] or "",
            "score": f"{r['home_score']}-{r['away_score']}",
            "jczq_h": _o(jczq.get("home")), "jczq_d": _o(jczq.get("draw")), "jczq_a": _o(jczq.get("away")),
            "sh_h": _o(sharp.get("home")), "sh_d": _o(sharp.get("draw")), "sh_a": _o(sharp.get("away")),
            "fav": fav, "fav_odds": _o(fav_odds), "hit": any_hit,
        })

    n = len(rows)
    hit = sum(1 for x in rows if x["hit"])
    print(f"均衡盘「平+让平」场次（锐利盘口径 T={T}，近{LAST_MATCHDAYS}比赛日） n={n}，命中 {hit}/{n}={hit/n*100 if n else 0:.1f}%")
    print("=" * 120)
    for x in sorted(rows, key=lambda z: (z["matchday"], z["match_num"])):
        fav_txt = ("主队" if x["fav"] == "home" else "客队") + f"({x['fav_odds']})"
        mark = "✓" if x["hit"] else "✗"
        print(f"{x['matchday']} {x['match_num'] or '':8s} {x['league'] or '':8s} {x['home']} vs {x['away']} [{x['score']}] {mark}")
        print(f"    竞彩HAD {x['jczq_h']}/{x['jczq_d']}/{x['jczq_a']} · 锐利盘 {x['sh_h']}/{x['sh_d']}/{x['sh_a']} · 让球方={fav_txt}")


if __name__ == "__main__":
    asyncio.run(main())
