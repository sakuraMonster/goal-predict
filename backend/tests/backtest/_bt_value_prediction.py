"""价值分析 · 预测提取 · 近7天命中回测（自包含，直接在 backend 容器内运行）。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_pred.py

口径：
  - 读取 value_analysis_snapshots 冻结的 1X2 best_side / HAD 赔率 / 让球赔率 → 重建预测提取；
  - 命中 = 单选项精确命中 / 双选项任一命中；让球项命中用 净胜球 + 让球线 判定；
  - 深盘阈值 DEEP_FAV_ODDS = 1.50。
"""
import sys
import os
import asyncio
import json

sys.path.insert(0, "/app")
import asyncpg

DEEP_FAV_ODDS = 1.50
LAST_MATCHDAYS = 7

MATCH_SQL = """
SELECT m.id, m.match_num, m.kickoff_time, m.home_score, m.away_score,
       l.name_zh AS league, ht.name_zh AS home_name, at.name_zh AS away_name,
       ((m.kickoff_time - interval '12 hours')::date)::text AS matchday
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN teams ht ON ht.id = m.home_team_id
LEFT JOIN teams at ON at.id = m.away_team_id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND m.kickoff_time >= now() - interval '25 days'
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


def _fav_from_had(had):
    if not isinstance(had, dict):
        return None
    h = _to_f(had.get("home"))
    a = _to_f(had.get("away"))
    if h is None or a is None or h <= 0 or a <= 0:
        return None
    if h < a:
        return "home"
    if a < h:
        return "away"
    return None


def extract_prediction(best_side, had_odds, has_hhad=True):
    if best_side not in ("home", "draw", "away"):
        return None
    fav = _fav_from_had(had_odds)
    fav_odds = _to_f((had_odds or {}).get(fav)) if fav else None
    if not has_hhad:
        lb = {"home": "胜", "draw": "平", "away": "负"}[best_side]
        return {"side": best_side, "mode": "had_only", "fav": None, "label": lb,
                "picks": [{"market": "had", "outcome": best_side, "label": lb}]}
    if best_side == "draw":
        if fav is None or fav_odds is None or fav_odds >= DEEP_FAV_ODDS:
            return {"side": "draw", "mode": "balanced_draw", "fav": fav, "label": "平 + 让平",
                    "picks": [{"market": "had", "outcome": "draw", "label": "平"},
                              {"market": "hhad", "outcome": "draw", "label": "让平"}]}
        lo, ll = ("away", "让负") if fav == "home" else ("home", "让胜")
        return {"side": "draw", "mode": "deep_draw", "fav": fav, "label": "让球方负 + 让球方平",
                "picks": [{"market": "hhad", "outcome": lo, "label": ll},
                          {"market": "hhad", "outcome": "draw", "label": "让平"}]}
    if best_side == "home":
        if fav == "away":
            return {"side": "home", "mode": "upset_win", "fav": fav, "label": "让胜",
                    "picks": [{"market": "hhad", "outcome": "home", "label": "让胜"}]}
        return {"side": "home", "mode": "favorite_win", "fav": fav, "label": "胜",
                "picks": [{"market": "had", "outcome": "home", "label": "胜"}]}
    # away
    if fav == "home":
        return {"side": "away", "mode": "upset_loss", "fav": fav, "label": "让负",
                "picks": [{"market": "hhad", "outcome": "away", "label": "让负"}]}
    return {"side": "away", "mode": "favorite_loss", "fav": fav, "label": "负",
            "picks": [{"market": "had", "outcome": "away", "label": "负"}]}


def actual_hhad(diff, line):
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
    if not isinstance(pred, dict):
        return None, []
    picks = pred.get("picks") or []
    if not picks:
        return None, []
    had_actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
    hhad_actual = actual_hhad(diff, line)
    hits = []
    for p in picks:
        if p.get("market") == "had":
            hits.append(had_actual == p.get("outcome"))
        else:
            hits.append(None if hhad_actual is None else (hhad_actual == p.get("outcome")))
    non = [h for h in hits if h is not None]
    return (any(non), hits) if non else (None, hits)


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

    # 取近 LAST_MATCHDAYS 个比赛日（按 kickoff desc 出现顺序）
    seen = []
    for r in mrows:
        md = r["matchday"]
        if md not in seen:
            seen.append(md)
        if len(seen) >= LAST_MATCHDAYS:
            break
    target = set(seen)
    rows = [r for r in mrows if r["matchday"] in target]

    records = []
    for r in rows:
        analysis = snap_map.get(r["id"])
        if not isinstance(analysis, dict):
            continue
        m1 = (analysis.get("markets") or {}).get("1x2") or {}
        best_side = (m1.get("value") or {}).get("best_side")
        had_odds = m1.get("sharp_odds")
        handicap = (analysis.get("markets") or {}).get("handicap") or {}
        has_hhad = bool(handicap.get("jczq_odds"))
        pred = extract_prediction(best_side, had_odds, has_hhad)
        if pred is None:
            continue
        hs = r["home_score"]
        aw = r["away_score"]
        diff = hs - aw
        actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        any_hit, pick_hits = hit_prediction(pred, diff, line_map.get(r["id"]))
        records.append({
            "matchday": r["matchday"],
            "match_num": r["match_num"],
            "league": r["league"],
            "home": r["home_name"] or r["match_num"],
            "away": r["away_name"] or "",
            "score": f"{hs}-{aw}",
            "best_side": best_side,
            "pred_label": pred["label"],
            "pred_mode": pred["mode"],
            "actual": actual,
            "any_hit": any_hit,
            "pick_hits": pick_hits,
        })

    # 按比赛日聚合（旧→新输出）
    by_day = {}
    for rc in records:
        by_day.setdefault(rc["matchday"], []).append(rc)

    print("=" * 100)
    print(f"价值分析 · 预测提取 · 近 {LAST_MATCHDAYS} 个比赛日命中回测  (DEEP_FAV_ODDS={DEEP_FAV_ODDS})")
    print("=" * 100)
    print(f"{'比赛日':>12} {'场':>3} {'命中':>3} {'命中率':>8}   {len(records)} 场有预测")
    print("-" * 100)

    total_n = 0
    total_hit = 0
    for md in sorted(by_day.keys()):
        rs = by_day[md]
        n = len(rs)
        hit = sum(1 for x in rs if x["any_hit"])
        rate = hit / n if n else 0.0
        total_n += n
        total_hit += hit
        print(f"{md:>12} {n:3d} {hit:3d} {rate*100:7.1f}%")

    print("-" * 100)
    print(f"{'合计':>12} {total_n:3d} {total_hit:3d} {(total_hit/total_n*100 if total_n else 0):7.1f}%")

    # 逐场明细
    print("\n" + "=" * 100)
    print("逐场明细")
    print("=" * 100)
    for md in sorted(by_day.keys()):
        print(f"\n【{md}】")
        for x in by_day[md]:
            mark = "✓" if x["any_hit"] is True else ("✗" if x["any_hit"] is False else "?")
            print(f"  {mark} {x['match_num'] or '':8s} {x['league'] or '':8s} {x['home']} vs {x['away']}"
                  f"  [{x['score']}]  提取={x['pred_label']}  实际={SIDE_ZH.get(x['actual'], '')}")


if __name__ == "__main__":
    asyncio.run(main())
