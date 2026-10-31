"""价值分析 · 「客让-负」1.5~2.0 区间的平赔细分（锐利盘口径）。

该区间目前保持提取「负」（客胜）。现拉出每场的主胜赔/平赔/客胜赔与实际赛果，
看平赔（或平赔相对主胜赔）能否把「客胜」与「主胜/平」区分开。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_favloss_15_20.py [比赛日数]
"""
import sys
import os
import asyncio
import json

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
        if pred.get("mode") != "favorite_loss":
            continue
        m1 = (a.get("markets") or {}).get("1x2") or {}
        sharp = m1.get("sharp_odds") or {}
        fav = pred.get("fav")
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        if fav != "away" or fav_odds is None or not (1.5 <= fav_odds < 2.0):
            continue
        home_odds = _to_f(sharp.get("home"))
        draw_odds = _to_f(sharp.get("draw"))
        hs, aw = r["home_score"], r["away_score"]
        diff = hs - aw
        had = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        line = _to_f(line_map.get(r["id"]))
        hhad = None
        if line is not None:
            adj = diff + line
            hhad = "home" if adj > 0 else ("draw" if adj == 0 else "away")
        hit = (had == "away")
        recs.append({
            "match_num": r["match_num"],
            "league": r["league"],
            "home": r["home_name"],
            "away": r["away_name"],
            "kickoff": str(r["kickoff_time"])[:16],
            "home_odds": home_odds,
            "draw_odds": draw_odds,
            "away_odds": fav_odds,
            "score": f"{hs}-{aw}",
            "had": had,
            "hhad": hhad,
            "hit": hit,
        })

    HAD_ZH = {"home": "主胜", "draw": "平", "away": "客胜"}
    HHAD_ZH = {"home": "让胜", "draw": "让平", "away": "让负"}

    # 按平赔升序
    recs.sort(key=lambda x: (x["draw_odds"] if x["draw_odds"] is not None else 999))

    n = len(recs)
    hit_n = sum(1 for x in recs if x["hit"])
    print(f"【客让-负 · 1.5~2.0 区间】近 {LAST_MATCHDAYS} 比赛日：n={n}，命中(客胜) {hit_n}，命中率 {hit_n/n*100:.1f}%\n")
    print(f"{'场次':10s} {'联赛':8s} {'对阵':26s} {'主胜赔':>6s} {'平赔':>6s} {'客胜赔':>6s} {'比分':>6s} {'赛果':>8s} {'结果':>4s}")
    print("-" * 100)
    for x in recs:
        flag = "命中" if x["hit"] else "未中"
        ho = f"{x['home_odds']:.2f}" if x["home_odds"] else "-"
        do = f"{x['draw_odds']:.2f}" if x["draw_odds"] else "-"
        ao = f"{x['away_odds']:.2f}" if x["away_odds"] else "-"
        print(f"{x['match_num']:10s} {x['league']:8s} {x['home'] + ' vs ' + x['away']:26s} "
              f"{ho:>6s} {do:>6s} {ao:>6s} {x['score']:>6s} {HAD_ZH[x['had']] + '/' + HHAD_ZH.get(x['hhad'],'?'):>8s} {flag:>4s}")


if __name__ == "__main__":
    asyncio.run(main())
