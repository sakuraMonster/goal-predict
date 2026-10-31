"""价值分析 · 「主让-负」(upset_loss) 与「客让-负」(favorite_loss) 按让方赔率分桶的具体数据。

命中口径：
  主让-负：实际让球胜平负 == 让负。
  客让-负：实际胜平负 == 客胜(负)。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_loss_bucket.py [比赛日数]
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


def bucket_upset(o):
    return "主让<1.4" if (o is not None and o < 1.4) else "主让1.8~2.0"


def bucket_favloss(o):
    if o is None:
        return "客让无"
    if o < 1.5:
        return "客让<1.5"
    if o < 2.0:
        return "客让1.5~2.0"
    if o < 2.3:
        return "客让2.0~2.3"
    if o < 2.6:
        return "客让2.3~2.6"
    return "客让>=2.6"


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
        mode = pred.get("mode")
        if mode not in ("upset_loss", "favorite_loss"):
            continue
        m1 = (a.get("markets") or {}).get("1x2") or {}
        sharp = m1.get("sharp_odds") or {}
        fav = pred.get("fav")
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        hs, aw = r["home_score"], r["away_score"]
        diff = hs - aw
        had = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        line = _to_f(line_map.get(r["id"]))
        hhad = None
        if line is not None:
            adj = diff + line
            hhad = "home" if adj > 0 else ("draw" if adj == 0 else "away")
        if mode == "upset_loss":
            hit = (hhad == "away")
            b = bucket_upset(fav_odds)
        else:
            hit = (had == "away")
            b = bucket_favloss(fav_odds)
        recs.append({
            "mode": mode,
            "match_num": r["match_num"],
            "league": r["league"],
            "home": r["home_name"],
            "away": r["away_name"],
            "kickoff": str(r["kickoff_time"])[:16],
            "fav": fav,
            "fav_odds": fav_odds,
            "score": f"{hs}-{aw}",
            "had": had,
            "hhad": hhad,
            "hit": hit,
            "bucket": b,
        })

    HAD_ZH = {"home": "主胜", "draw": "平", "away": "客胜"}
    HHAD_ZH = {"home": "让胜", "draw": "让平", "away": "让负"}

    groups = [
        ("upset_loss", "主让-负（冷门负 → 让负）", ["主让<1.4", "主让1.8~2.0"]),
        ("favorite_loss", "客让-负（正路负 → 负）", ["客让<1.5", "客让1.5~2.0", "客让2.0~2.3", "客让2.3~2.6", "客让>=2.6"]),
    ]

    for mode, title, order in groups:
        sub_all = [x for x in recs if x["mode"] == mode]
        n = len(sub_all)
        hit_n = sum(1 for x in sub_all if x["hit"])
        print(f"【{title}】近 {LAST_MATCHDAYS} 比赛日：n={n}，命中 {hit_n}，命中率 {hit_n/n*100:.1f}%\n")
        g = defaultdict(list)
        for x in sub_all:
            g[x["bucket"]].append(x)
        for b in order:
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
            print(f"  桶 {b}: n={len(sub)}, 命中 {hn} ({hn/len(sub)*100:.1f}%)  HAD={had_s}  HHAD={hhad_s}")
            for x in sub:
                flag = "命中" if x["hit"] else "未中"
                print(f"     {x['match_num']} {x['league']} {x['home']} vs {x['away']} ({x['fav_odds']}) {x['score']} {HAD_ZH[x['had']]}/{HHAD_ZH.get(x['hhad'],'?')} {flag}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
