"""价值分析 · 预测提取 · 近 N 比赛日逐场明细（含赔率），直接在 backend 容器内运行。

输出每场：场次/联赛/对阵/比分、竞彩HAD、锐利盘1X2、让球方(锐利盘胜赔)、预测提取、命中。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_pred_detail.py [比赛日数]
"""
import sys
import os
import asyncio
import json

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
DEEP_FAV_ODDS = 1.50

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

SNAP_SQL = """
SELECT match_id, analysis_json
FROM value_analysis_snapshots
WHERE match_id = ANY($1)
"""

LINE_SQL = """
SELECT DISTINCT ON (match_id) match_id, hhad_line
FROM jczq_play_odds_snapshots
WHERE match_id = ANY($1)
ORDER BY match_id, snapshot_time DESC
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


def extract_prediction(best_side, sharp, has_hhad=True):
    if best_side not in ("home", "draw", "away"):
        return None
    fav = _fav_from_1x2(sharp)
    fav_odds = _to_f((sharp or {}).get(fav)) if fav else None
    if not has_hhad:
        lb = {"home": "胜", "draw": "平", "away": "负"}[best_side]
        return {"mode": "had_only", "label": lb, "fav": None}
    if best_side == "draw":
        if fav is None or fav_odds is None or fav_odds >= DEEP_FAV_ODDS:
            return {"mode": "balanced_draw", "label": "平 + 让平", "fav": fav}
        return {"mode": "deep_draw", "label": "让球方负 + 让球方平", "fav": fav}
    if best_side == "home":
        if fav == "away":
            return {"mode": "upset_win", "label": "让胜", "fav": fav}
        return {"mode": "favorite_win", "label": "胜", "fav": fav}
    if fav == "home":
        return {"mode": "upset_loss", "label": "让负", "fav": fav}
    return {"mode": "favorite_loss", "label": "负", "fav": fav}


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
    fav = pred["fav"]
    if mode == "favorite_win":
        picks = [("had", "home")]
    elif mode == "favorite_loss":
        picks = [("had", "away")]
    elif mode == "upset_win":
        picks = [("hhad", "home")]
    elif mode == "upset_loss":
        picks = [("hhad", "away")]
    elif mode == "balanced_draw":
        picks = [("had", "draw"), ("hhad", "draw")]
    elif mode == "deep_draw":
        picks = [("hhad", "away" if fav == "home" else "home"), ("hhad", "draw")]
    else:  # had_only
        outcome = {"胜": "home", "平": "draw", "负": "away"}.get(pred["label"])
        picks = [("had", outcome)] if outcome else []
    had_actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
    hhad_actual = _actual_hhad(diff, line)
    hits = []
    for market, outcome in picks:
        if market == "had":
            hits.append(had_actual == outcome)
        else:
            hits.append(None if hhad_actual is None else (hhad_actual == outcome))
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


SIDE_ZH = {"home": "主胜", "draw": "平", "away": "客胜"}
FAV_ZH = {"home": "主队", "away": "客队"}
MODE_ZH = {
    "favorite_win": "正路胜", "upset_win": "冷门胜",
    "favorite_loss": "正路负", "upset_loss": "冷门负",
    "balanced_draw": "均衡平", "deep_draw": "深盘平", "had_only": "仅SPF",
}


def _o(v):
    return "—" if v is None else f"{v:.2f}"


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    mrows = await conn.fetch(MATCH_SQL)
    mids = [r["id"] for r in mrows]
    snap_map = {}
    line_map = {}
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

    rows = [r for r in mrows if r["matchday"] in target]

    print(f"价值分析 · 预测提取 · 近 {LAST_MATCHDAYS} 个比赛日逐场明细（锐利盘口径，深盘阈值={DEEP_FAV_ODDS}）")
    print("=" * 130)
    for r in sorted(rows, key=lambda x: (x["matchday"], x["kickoff_time"])):
        analysis = snap_map.get(r["id"])
        if not isinstance(analysis, dict):
            continue
        m1 = (analysis.get("markets") or {}).get("1x2") or {}
        best_side = (m1.get("value") or {}).get("best_side")
        jczq = m1.get("jczq_odds") or {}
        sharp = m1.get("sharp_odds") or {}
        handicap = (analysis.get("markets") or {}).get("handicap") or {}
        has_hhad = bool(handicap.get("jczq_odds"))
        if best_side not in ("home", "draw", "away"):
            continue

        pred = extract_prediction(best_side, sharp, has_hhad)
        diff = r["home_score"] - r["away_score"]
        actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        hit = hit_prediction(pred, diff, line_map.get(r["id"]))
        mark = "✓" if hit is True else ("✗" if hit is False else "?")

        fav = pred["fav"]
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        fav_txt = f"{FAV_ZH.get(fav, '无')}({_o(fav_odds)})" if fav else "无"

        print(f"{r['matchday']} {r['match_num'] or '':8s} {r['league'] or '':8s} "
              f"{r['home_name'] or r['match_num']} vs {r['away_name'] or ''} [{r['home_score']}-{r['away_score']}]")
        print(f"    竞彩HAD {_o(jczq.get('home'))}/{_o(jczq.get('draw'))}/{_o(jczq.get('away'))} "
              f"· 锐利盘 {_o(sharp.get('home'))}/{_o(sharp.get('draw'))}/{_o(sharp.get('away'))} "
              f"· 让球方={fav_txt}")
        print(f"    预测提取={pred['label']}({MODE_ZH.get(pred['mode'], pred['mode'])}) {mark}  "
              f"实际={SIDE_ZH[actual]}  价值分析原始方向={SIDE_ZH.get(best_side, '')}")


if __name__ == "__main__":
    asyncio.run(main())
