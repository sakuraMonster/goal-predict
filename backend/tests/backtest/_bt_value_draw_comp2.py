"""价值分析 · 「平+让平」(均衡盘平) 按让球方主客 × 让球方锐利盘胜赔 的赛果构成（锐利盘口径）。

目的：像之前负/胜场景一样，按让球方赔率分桶，看每桶的实际赛果构成（HAD 主胜/平/客胜 与 HHAD 让胜/让平/让负），
以找出「平+让平」在哪些桶该改口径。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_draw_comp2.py [比赛日数]
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
        had = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        hhad = None
        if line is not None:
            adj = diff + line
            hhad = "home" if adj > 0 else ("draw" if adj == 0 else "away")

        recs.append({"fav": fav, "fav_odds": fav_odds, "had": had, "hhad": hhad})

    def fav_bucket(o):
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

    HAD_ZH = {"home": "主胜", "draw": "平", "away": "客胜"}
    HHAD_ZH = {"home": "让胜", "draw": "让平", "away": "让负"}

    for side, side_zh in [("home", "主让"), ("away", "客让")]:
        sub = [x for x in recs if x["fav"] == side]
        print(f"\n【{side_zh}】 n={len(sub)}")
        g = defaultdict(lambda: {"had": defaultdict(int), "hhad": defaultdict(int)})
        for x in sub:
            b = fav_bucket(x["fav_odds"])
            g[b]["had"][x["had"]] += 1
            if x["hhad"] is not None:
                g[b]["hhad"][x["hhad"]] += 1
        for b in sorted(g, key=lambda k: str(k)):
            d = g[b]
            had_s = {HAD_ZH.get(k, k): v for k, v in d["had"].items()}
            hhad_s = {HHAD_ZH.get(k, k): v for k, v in d["hhad"].items()}
            print(f"  让方赔率 {b:8s} n={sum(d['had'].values()):2d}  HAD={had_s}  HHAD={hhad_s}")


if __name__ == "__main__":
    asyncio.run(main())
