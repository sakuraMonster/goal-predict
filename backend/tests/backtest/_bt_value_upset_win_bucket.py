"""价值分析 · 「客让-胜」(mode=upset_win) 按客让赔率分桶的具体数据（锐利盘口径）。

命中口径：提取「让胜」（hhad=home），实际让球胜平负 == 让胜 即命中。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_upset_win_bucket.py [比赛日数]
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


def _load_json(v):
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def bucket(o):
    if o is None:
        return "无"
    if o < 1.5:
        return "<1.5"
    if o < 1.8:
        return "1.5~1.8"
    if o < 2.0:
        return "1.8~2.0"
    if o < 2.3:
        return "2.0~2.3"
    if o < 2.6:
        return "2.3~2.6"
    return ">=2.6"


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
        pred = a.get("prediction")
        if not isinstance(pred, dict):
            continue
        if pred.get("mode") != "upset_win":
            continue
        m1 = (a.get("markets") or {}).get("1x2") or {}
        sharp = m1.get("sharp_odds") or {}
        away_odds = _to_f(sharp.get("away"))
        hs, aw = r["home_score"], r["away_score"]
        diff = hs - aw
        had = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        line = _to_f(line_map.get(r["id"]))
        hhad = None
        if line is not None:
            adj = diff + line
            hhad = "home" if adj > 0 else ("draw" if adj == 0 else "away")
        hit = (hhad == "home")
        recs.append({
            "match_num": r["match_num"],
            "league": r["league"],
            "home": r["home_name"],
            "away": r["away_name"],
            "kickoff": str(r["kickoff_time"])[:16],
            "away_odds": away_odds,
            "score": f"{hs}-{aw}",
            "had": had,
            "hhad": hhad,
            "hit": hit,
            "bucket": bucket(away_odds),
        })

    HAD_ZH = {"home": "主胜", "draw": "平", "away": "客胜"}
    HHAD_ZH = {"home": "让胜", "draw": "让平", "away": "让负"}
    ORDER = ["<1.5", "1.5~1.8", "1.8~2.0", "2.0~2.3", "2.3~2.6", ">=2.6"]

    n = len(recs)
    hit_n = sum(1 for x in recs if x["hit"])
    print(f"【客让-胜】按客让赔率分桶（近 {LAST_MATCHDAYS} 比赛日）：n={n}，命中 {hit_n}，命中率 {hit_n/n*100:.1f}%\n")

    g = defaultdict(list)
    for x in recs:
        g[x["bucket"]].append(x)

    for b in ORDER:
        sub = g.get(b)
        if not sub:
            continue
        hn = sum(1 for x in sub if x["hit"])
        had_c = defaultdict(int)
        hhad_c = defaultdict(int)
        for x in sub:
            had_c[x["had"]] += 1
            if x["hhad"] is not None:
                hhad_c[x["hhad"]] += 1
        had_s = {HAD_ZH[k]: v for k, v in had_c.items()}
        hhad_s = {HHAD_ZH[k]: v for k, v in hhad_c.items()}
        print(f"桶 客让 {b}: n={len(sub)}, 命中 {hn} ({hn/len(sub)*100:.1f}%)")
        print(f"   HAD={had_s}  HHAD={hhad_s}")
        for x in sub:
            flag = "命中" if x["hit"] else "未中"
            hhad_zh = HHAD_ZH.get(x["hhad"], "?")
            print(f"     {x['match_num']} {x['league']} {x['home']} vs {x['away']} ({x['away_odds']}) {x['score']} {HAD_ZH[x['had']]}/{hhad_zh} {flag}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
