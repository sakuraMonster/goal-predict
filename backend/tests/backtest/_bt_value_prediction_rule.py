"""价值分析 · 预测提取 · 加入「均衡盘分出胜负」规则后的命中回测（锐利盘口径）。

新规则（追加）：
  均衡盘平预测，若让球方锐利盘胜赔落在 [1.80, 2.00)：
    客让 → 胜 + 让负（主胜 或 客净胜2+）
    主让 → 负 + 让胜（客胜 或 主净胜2+）
  其余不变。

命中口径：单选项精确命中 / 双选项任一命中。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_rule.py [比赛日数]
"""
import sys
import os
import asyncio
import json
from collections import defaultdict

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
ODDS_SRC = sys.argv[2] if len(sys.argv) > 2 else "sharp"  # "sharp"(锐利盘) | "jczq"(竞彩)
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
DRAW_HOME_FAV_LO = 1.50
DRAW_HOME_FAV_HI = 1.60
DRAW_AWAY_PUSH_LO = 1.60
DRAW_AWAY_PUSH_HI = 1.80
DRAW_AWAY_DECISIVE_LO = 2.00
DRAW_AWAY_DECISIVE_HI = 2.30
UPSET_WIN_DEEP_ODDS = 1.50
UPSET_WIN_DRAW_LOSS_LO = 2.00
UPSET_WIN_DRAW_LOSS_HI = 2.30
UPSET_WIN_MID_HI = 2.60
UPSET_WIN_LOW_DRAW = 3.50
UPSET_WIN_HIGH_DRAW = 4.00
UPSET_LOSS_DEEP_ODDS = 1.40
UPSET_LOSS_MID_LO = 1.80
UPSET_LOSS_MID_HI = 2.00
FAVORITE_LOSS_DEEP_ODDS = 1.50
FAVORITE_LOSS_DRAW_HOME_LO = 2.30
FAVORITE_LOSS_DRAW_HOME_HI = 2.60
BALANCED_DRAW_LOW_DRAW = 3.50
BALANCED_DRAW_HIGH_DRAW = 4.00

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


def _pick(market, outcome, label):
    return {"market": market, "outcome": outcome, "label": label}


def extract_prediction(best_side, sharp, has_hhad=True):
    if best_side not in ("home", "draw", "away"):
        return None
    fav = _fav_from_1x2(sharp)
    fav_odds = _to_f((sharp or {}).get(fav)) if fav else None
    draw_odds = _to_f((sharp or {}).get("draw"))

    if not has_hhad:
        lb = {"home": "胜", "draw": "平", "away": "负"}[best_side]
        return {"side": best_side, "mode": "had_only", "label": lb,
                "picks": [_pick("had", best_side, lb)]}

    if best_side == "draw":
        if fav is None or fav_odds is None or fav_odds >= DEEP_FAV_ODDS:
            if fav is not None and fav_odds is not None and DECISIVE_LO <= fav_odds < DECISIVE_HI:
                if fav == "home":
                    return {"side": "draw", "mode": "decisive_draw_home", "label": "负 + 让胜",
                            "picks": [_pick("had", "away", "负"), _pick("hhad", "home", "让胜")]}
                return {"side": "draw", "mode": "decisive_draw_away", "label": "胜 + 让负",
                        "picks": [_pick("had", "home", "胜"), _pick("hhad", "away", "让负")]}
            if fav == "home" and fav_odds is not None and DRAW_HOME_FAV_LO <= fav_odds < DRAW_HOME_FAV_HI:
                return {"side": "draw", "mode": "draw_home_fav_win", "label": "让胜 + 平",
                        "picks": [_pick("hhad", "home", "让胜"), _pick("had", "draw", "平")]}
            if fav == "away" and fav_odds is not None and DRAW_AWAY_PUSH_LO <= fav_odds < DRAW_AWAY_PUSH_HI:
                return {"side": "draw", "mode": "draw_away_fav_push", "label": "让胜 + 负",
                        "picks": [_pick("hhad", "home", "让胜"), _pick("had", "away", "负")]}
            if fav == "away" and fav_odds is not None and DRAW_AWAY_DECISIVE_LO <= fav_odds < DRAW_AWAY_DECISIVE_HI:
                return {"side": "draw", "mode": "draw_away_fav_decisive", "label": "胜 + 负",
                        "picks": [_pick("had", "home", "胜"), _pick("had", "away", "负")]}
            if fav == "home" and draw_odds is not None and draw_odds < BALANCED_DRAW_LOW_DRAW:
                return {"side": "draw", "mode": "balanced_draw_home_low", "label": "让胜 + 平",
                        "picks": [_pick("hhad", "home", "让胜"), _pick("had", "draw", "平")]}
            if fav == "home" and draw_odds is not None and draw_odds >= BALANCED_DRAW_HIGH_DRAW:
                return {"side": "draw", "mode": "balanced_draw_home_high", "label": "让胜 + 让平",
                        "picks": [_pick("hhad", "home", "让胜"), _pick("hhad", "draw", "让平")]}
            return {"side": "draw", "mode": "balanced_draw", "label": "平 + 让平",
                    "picks": [_pick("had", "draw", "平"), _pick("hhad", "draw", "让平")]}
        if fav == "home":
            return {"side": "draw", "mode": "deep_draw_home", "label": "让胜 + 让平",
                    "picks": [_pick("hhad", "home", "让胜"), _pick("hhad", "draw", "让平")]}
        return {"side": "draw", "mode": "deep_draw_away", "label": "让负 + 让平",
                "picks": [_pick("hhad", "away", "让负"), _pick("hhad", "draw", "让平")]}

    if best_side == "home":
        if fav == "away":
            if fav_odds is not None and fav_odds < UPSET_WIN_DEEP_ODDS:
                return {"side": "home", "mode": "upset_win_deep", "label": "让平 + 让负",
                        "picks": [_pick("hhad", "draw", "让平"), _pick("hhad", "away", "让负")]}
            if fav_odds is not None and UPSET_WIN_DRAW_LOSS_LO <= fav_odds < UPSET_WIN_DRAW_LOSS_HI:
                return {"side": "home", "mode": "upset_win_draw_loss", "label": "平 + 负",
                        "picks": [_pick("had", "draw", "平"), _pick("had", "away", "负")]}
            if fav_odds is not None and fav_odds < UPSET_WIN_DRAW_LOSS_LO:
                if draw_odds is not None and draw_odds >= UPSET_WIN_HIGH_DRAW:
                    return {"side": "home", "mode": "upset_win", "label": "让胜",
                            "picks": [_pick("hhad", "home", "让胜")]}
                return {"side": "home", "mode": "upset_win_mid_lowdraw", "label": "让负 + 平",
                        "picks": [_pick("hhad", "away", "让负"), _pick("had", "draw", "平")]}
            if fav_odds is not None and fav_odds < UPSET_WIN_MID_HI:
                if draw_odds is not None and draw_odds < UPSET_WIN_LOW_DRAW:
                    return {"side": "home", "mode": "upset_win_mid_low", "label": "平 + 负",
                            "picks": [_pick("had", "draw", "平"), _pick("had", "away", "负")]}
                return {"side": "home", "mode": "upset_win_mid_push", "label": "平 + 让平",
                        "picks": [_pick("had", "draw", "平"), _pick("hhad", "draw", "让平")]}
            return {"side": "home", "mode": "upset_win_high_draw", "label": "平 + 负",
                    "picks": [_pick("had", "draw", "平"), _pick("had", "away", "负")]}
        if fav_odds is not None and FAV_WIN_DRAW_LO <= fav_odds < FAV_WIN_DRAW_HI:
            return {"side": "home", "mode": "fav_win_draw", "label": "胜 + 平",
                    "picks": [_pick("had", "home", "胜"), _pick("had", "draw", "平")]}
        if fav_odds is not None and fav_odds >= FAV_NO_WIN_LO:
            if draw_odds is not None and draw_odds < FAV_NO_WIN_DRAW_SPLIT:
                return {"side": "home", "mode": "fav_no_win_draw_loss", "label": "平 + 负",
                        "picks": [_pick("had", "draw", "平"), _pick("had", "away", "负")]}
            return {"side": "home", "mode": "fav_no_win_loss", "label": "负",
                    "picks": [_pick("had", "away", "负")]}
        return {"side": "home", "mode": "favorite_win", "label": "胜",
                "picks": [_pick("had", "home", "胜")]}
    if fav == "home":
        if fav_odds is not None and fav_odds < UPSET_LOSS_DEEP_ODDS:
            return {"side": "away", "mode": "upset_loss_deep", "label": "让胜 + 让负",
                    "picks": [_pick("hhad", "home", "让胜"), _pick("hhad", "away", "让负")]}
        if fav_odds is not None and FAV_COVER_LO <= fav_odds < FAV_COVER_HI:
            return {"side": "away", "mode": "fav_cover", "label": "让胜",
                    "picks": [_pick("hhad", "home", "让胜")]}
        if fav_odds is not None and FAV_PUSH_LO <= fav_odds < FAV_PUSH_HI:
            return {"side": "away", "mode": "fav_push_loss", "label": "让平 + 负",
                    "picks": [_pick("hhad", "draw", "让平"), _pick("had", "away", "负")]}
        if fav_odds is not None and UPSET_LOSS_MID_LO <= fav_odds < UPSET_LOSS_MID_HI:
            return {"side": "away", "mode": "upset_loss_mid", "label": "胜 + 平",
                    "picks": [_pick("had", "home", "胜"), _pick("had", "draw", "平")]}
        if fav_odds is not None and fav_odds >= FAV_DRAW_LO:
            return {"side": "away", "mode": "fav_push_draw", "label": "让平 + 平",
                    "picks": [_pick("hhad", "draw", "让平"), _pick("had", "draw", "平")]}
        return {"side": "away", "mode": "upset_loss", "label": "让负",
                "picks": [_pick("hhad", "away", "让负")]}
    if fav_odds is not None and fav_odds < FAVORITE_LOSS_DEEP_ODDS:
        return {"side": "away", "mode": "favorite_loss_deep", "label": "让平 + 让负",
                "picks": [_pick("hhad", "draw", "让平"), _pick("hhad", "away", "让负")]}
    if fav_odds is not None and FAVORITE_LOSS_DRAW_HOME_LO <= fav_odds < FAVORITE_LOSS_DRAW_HOME_HI:
        return {"side": "away", "mode": "favorite_loss_draw_home", "label": "胜 + 平",
                "picks": [_pick("had", "home", "胜"), _pick("had", "draw", "平")]}
    return {"side": "away", "mode": "favorite_loss", "label": "负",
            "picks": [_pick("had", "away", "负")]}


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
    if not isinstance(pred, dict):
        return None
    picks = pred.get("picks") or []
    if not picks:
        return None
    had_actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
    hhad_actual = _actual_hhad(diff, line)
    hits = []
    for p in picks:
        if p["market"] == "had":
            hits.append(had_actual == p["outcome"])
        else:
            hits.append(None if hhad_actual is None else (hhad_actual == p["outcome"]))
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

    stats = defaultdict(lambda: [0, 0])
    for r in mrows:
        if r["matchday"] not in target:
            continue
        a = snap_map.get(r["id"])
        if not isinstance(a, dict):
            continue
        m1 = (a.get("markets") or {}).get("1x2") or {}
        best_side = (m1.get("value") or {}).get("best_side")
        odds = m1.get("sharp_odds" if ODDS_SRC == "sharp" else "jczq_odds") or {}
        handicap = (a.get("markets") or {}).get("handicap") or {}
        has_hhad = bool(handicap.get("jczq_odds"))
        if best_side not in ("home", "draw", "away"):
            continue
        pred = extract_prediction(best_side, odds, has_hhad)
        if pred is None:
            continue
        diff = r["home_score"] - r["away_score"]
        hit = hit_prediction(pred, diff, line_map.get(r["id"]))
        if hit is None:
            continue
        stats[pred["label"]][0] += 1
        if hit:
            stats[pred["label"]][1] += 1

    total_n = sum(v[0] for v in stats.values())
    total_hit = sum(v[1] for v in stats.values())
    src_zh = "锐利盘" if ODDS_SRC == "sharp" else "竞彩"
    print(f"价值分析 · 预测提取 · 命中回测（加入「均衡盘分出胜负」规则，{src_zh}口径，近{LAST_MATCHDAYS}比赛日）")
    print("=" * 80)
    print(f"{'预测提取':24s} {'场次':>4} {'命中':>4} {'命中率':>8}")
    print("-" * 80)
    for label in sorted(stats, key=lambda k: -stats[k][0]):
        n, h = stats[label]
        print(f"{label:24s} {n:4d} {h:4d} {h/n*100:7.1f}%")
    print("-" * 80)
    print(f"{'合计':24s} {total_n:4d} {total_hit:4d} {total_hit/total_n*100 if total_n else 0:7.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
