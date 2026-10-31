"""价值分析 · 「客让-胜」(upset_win，客队让球·优选主胜→让胜) 的具体场次。

注：客让<1.5 与客让2.0~2.3 已细分出去，此脚本只列剩余兜底 mode=upset_win。
命中口径：让胜（hhad==home，主队受让赢盘）。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_upset_win.py [比赛日数]
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
        if pred.get("mode") != "upset_win":
            continue
        m1 = (a.get("markets") or {}).get("1x2") or {}
        sharp = m1.get("sharp_odds") or {}
        fav = pred.get("fav")
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        draw_odds = _to_f(sharp.get("draw"))
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
            "fav_odds": fav_odds,
            "draw_odds": draw_odds,
            "score": f"{hs}-{aw}",
            "had": had,
            "hhad": hhad,
            "hit": hit,
        })

    HAD_ZH = {"home": "主胜", "draw": "平", "away": "客胜"}
    HHAD_ZH = {"home": "让胜", "draw": "让平", "away": "让负"}

    recs.sort(key=lambda x: (x["fav_odds"] if x["fav_odds"] is not None else 999))
    n = len(recs)
    hit_n = sum(1 for x in recs if x["hit"])
    print(f"【客让-胜】(upset_win 兜底) 近 {LAST_MATCHDAYS} 比赛日：n={n}，命中(让胜) {hit_n}，命中率 {hit_n/n*100:.1f}%\n")
    for x in recs:
        flag = "命中" if x["hit"] else "未中"
        fo = f"{x['fav_odds']:.2f}" if x["fav_odds"] else "-"
        do = f"{x['draw_odds']:.2f}" if x["draw_odds"] else "-"
        print(f"{x['match_num']} {x['league']} {x['home']} vs {x['away']} 客让{fo} 平赔{do} {x['score']} {HAD_ZH[x['had']]}/{HHAD_ZH.get(x['hhad'],'?')} {flag}")


if __name__ == "__main__":
    asyncio.run(main())
