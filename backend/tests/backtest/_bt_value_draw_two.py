"""价值分析 · 对比「平+让平」与「让平+平」两组逐场明细（锐利盘口径，全部规则）。

两组 pick 相同（HAD平 + HHAD让平），仅来源不同：
  平+让平(balanced_draw)：价值分析优选平。
  让平+平(fav_push_draw)：价值分析优选负 + 主队让球方 + 主队锐利盘赔率>=2.0。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_draw_two.py [比赛日数]
"""
import sys
import os
import asyncio
import json

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
DEEP_FAV_ODDS = 1.50
DECISIVE_LO = 1.80
DECISIVE_HI = 2.00
FAV_COVER_LO = 1.40
FAV_COVER_HI = 1.60
FAV_PUSH_LO = 1.60
FAV_PUSH_HI = 1.80
FAV_DRAW_LO = 2.00
FAV_WIN_DRAW_LO = 2.00
FAV_WIN_DRAW_HI = 2.30
FAV_NO_WIN_LO = 2.60
FAV_NO_WIN_DRAW_SPLIT = 3.40

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


def _pick(m, o, l):
    return {"market": m, "outcome": o, "label": l}


def extract_prediction(best_side, sharp, has_hhad=True):
    if best_side not in ("home", "draw", "away"):
        return None
    fav = _fav_from_1x2(sharp)
    fav_odds = _to_f((sharp or {}).get(fav)) if fav else None
    draw_odds = _to_f((sharp or {}).get("draw"))
    if not has_hhad:
        lb = {"home": "胜", "draw": "平", "away": "负"}[best_side]
        return {"mode": "had_only", "label": lb}
    if best_side == "draw":
        if fav is None or fav_odds is None or fav_odds >= DEEP_FAV_ODDS:
            if fav is not None and fav_odds is not None and DECISIVE_LO <= fav_odds < DECISIVE_HI:
                if fav == "home":
                    return {"mode": "decisive_draw_home", "label": "负 + 让胜"}
                return {"mode": "decisive_draw_away", "label": "胜 + 让负"}
            return {"mode": "balanced_draw", "label": "平 + 让平"}
        return {"mode": "deep_draw", "label": "让球方负 + 让球方平"}
    if best_side == "home":
        if fav == "away":
            return {"mode": "upset_win", "label": "让胜"}
        if fav_odds is not None and FAV_WIN_DRAW_LO <= fav_odds < FAV_WIN_DRAW_HI:
            return {"mode": "fav_win_draw", "label": "胜 + 平"}
        if fav_odds is not None and fav_odds >= FAV_NO_WIN_LO:
            if draw_odds is not None and draw_odds < FAV_NO_WIN_DRAW_SPLIT:
                return {"mode": "fav_no_win_draw_loss", "label": "平 + 负"}
            return {"mode": "fav_no_win_loss", "label": "负"}
        return {"mode": "favorite_win", "label": "胜"}
    # away
    if fav == "home":
        if fav_odds is not None and FAV_COVER_LO <= fav_odds < FAV_COVER_HI:
            return {"mode": "fav_cover", "label": "让胜"}
        if fav_odds is not None and FAV_PUSH_LO <= fav_odds < FAV_PUSH_HI:
            return {"mode": "fav_push_loss", "label": "让平 + 负"}
        if fav_odds is not None and fav_odds >= FAV_DRAW_LO:
            return {"mode": "fav_push_draw", "label": "让平 + 平"}
        return {"mode": "upset_loss", "label": "让负"}
    return {"mode": "favorite_loss", "label": "负"}


def _actual_hhad(diff, line):
    l = _to_f(line)
    if l is None:
        return None
    adj = diff + l
    if adj > 0:
        return "home"
    if adj == 0:
        return "draw"
    return "away"


def hit_prediction(pred, diff, line):
    mode = pred["mode"]
    if mode == "balanced_draw":
        picks = [("had", "draw"), ("hhad", "draw")]
    elif mode == "fav_push_draw":
        picks = [("hhad", "draw"), ("had", "draw")]
    else:
        return None
    had_actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
    hhad_actual = _actual_hhad(diff, line)
    hits = []
    for m, o in picks:
        if m == "had":
            hits.append(had_actual == o)
        else:
            hits.append(None if hhad_actual is None else (hhad_actual == o))
    non = [h for h in hits if h is not None]
    return (any(non) if non else None)


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

    groups = {"balanced_draw": [], "fav_push_draw": []}
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
        if best_side not in ("home", "draw", "away"):
            continue
        pred = extract_prediction(best_side, sharp, has_hhad)
        if pred is None or pred["mode"] not in groups:
            continue
        diff = r["home_score"] - r["away_score"]
        hit = hit_prediction(pred, diff, line_map.get(r["id"]))
        fav = _fav_from_1x2(sharp)
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        groups[pred["mode"]].append({
            "matchday": r["matchday"], "match_num": r["match_num"], "league": r["league"],
            "home": r["home_name"], "away": r["away_name"],
            "score": f"{r['home_score']}-{r['away_score']}",
            "sh_h": sharp.get("home"), "sh_d": sharp.get("draw"), "sh_a": sharp.get("away"),
            "fav": fav, "fav_odds": fav_odds, "line": line_map.get(r["id"]),
            "hit": hit,
        })

    for mode, title in [("balanced_draw", "平 + 让平（价值分析优选平）"), ("fav_push_draw", "让平 + 平（价值分析优选负 + 主让≥2.0）")]:
        rows = groups[mode]
        n = len(rows)
        hit = sum(1 for x in rows if x["hit"])
        print("\n" + "=" * 100)
        print(f"【{title}】 n={n} 命中 {hit}/{n}={hit/n*100 if n else 0:.1f}%")
        print("-" * 100)
        for x in sorted(rows, key=lambda z: (z["matchday"], z["match_num"])):
            mark = "✓" if x["hit"] else "✗"
            fav_txt = ("主" if x["fav"] == "home" else "客") + f"({_o(x['fav_odds'])})"
            print(f"  {x['matchday']} {x['match_num'] or '':8s} {x['league'] or '':8s} {x['home']} vs {x['away']} [{x['score']}] {mark}")
            print(f"      锐利盘 主{_o(x['sh_h'])}/平{_o(x['sh_d'])}/客{_o(x['sh_a'])} · 让球方={fav_txt} · 让球线={_o(x['line'])}")


if __name__ == "__main__":
    asyncio.run(main())
