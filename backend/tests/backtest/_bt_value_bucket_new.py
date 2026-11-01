"""价值分析 · 规则提取 · 初盘口径 平赔拆分子规则 命中验证。

针对两条低命中规则，按用户指定平赔边界拆分，并应用新提取结果，验证命中率。

拆分映射（初盘口径，best_side 尾盘冻结）：
  客让-负：
    平赔 <4.0   -> 让胜 + 让平
    平赔 >=4.0  -> 让负 + 让胜
  主让1.4~1.6-负：
    平赔 <4.5     -> 胜
    平赔 4.5~5.0  -> 让胜 + 让平
    平赔 >5.0     -> 让负

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_bucket_new.py [比赛日数]
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
DECISIVE_FAV_LO = 1.80
DECISIVE_FAV_HI = 2.00
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

OPEN_PINNACLE_SQL = """
SELECT DISTINCT ON (match_id) match_id, home_win, draw, away_win
FROM odds_snapshots
WHERE match_id = ANY($1) AND bookmaker = 'Pinnacle'
  AND home_win IS NOT NULL AND draw IS NOT NULL AND away_win IS NOT NULL
ORDER BY match_id, snapshot_time ASC
"""
OPEN_BET365_SQL = """
SELECT DISTINCT ON (match_id) match_id, home_win, draw, away_win
FROM odds_snapshots
WHERE match_id = ANY($1) AND bookmaker = 'bet365'
  AND home_win IS NOT NULL AND draw IS NOT NULL AND away_win IS NOT NULL
ORDER BY match_id, snapshot_time ASC
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


def extract_prediction(best_side, sharp_1x2, has_hhad=True):
    if best_side not in ("home", "draw", "away"):
        return None
    fav = _fav_from_1x2(sharp_1x2)
    fav_odds = _to_f((sharp_1x2 or {}).get(fav)) if fav else None
    draw_odds = _to_f((sharp_1x2 or {}).get("draw"))

    if not has_hhad:
        side_label = {"home": "胜", "draw": "平", "away": "负"}[best_side]
        rule_label = {"home": "无盘-胜", "draw": "无盘-平", "away": "无盘-负"}[best_side]
        return {"side": best_side, "mode": "had_only", "fav": None,
                "label": rule_label, "picks": [_pick("had", best_side, side_label)]}

    if best_side == "draw":
        if fav is None or fav_odds is None or fav_odds >= DEEP_FAV_ODDS:
            if fav is not None and fav_odds is not None and DECISIVE_FAV_LO <= fav_odds < DECISIVE_FAV_HI:
                if fav == "home":
                    return {"side": "draw", "mode": "decisive_draw_home", "fav": fav, "label": "主让1.8~2.0-平",
                            "picks": [_pick("had", "away", "负"), _pick("hhad", "home", "让胜")]}
                return {"side": "draw", "mode": "decisive_draw_away", "fav": fav, "label": "客让1.8~2.0-平",
                        "picks": [_pick("had", "home", "胜"), _pick("hhad", "away", "让负")]}
            if fav == "home" and fav_odds is not None and DRAW_HOME_FAV_LO <= fav_odds < DRAW_HOME_FAV_HI:
                return {"side": "draw", "mode": "draw_home_fav_win", "fav": fav, "label": "主让1.5~1.6-平",
                        "picks": [_pick("hhad", "home", "让胜"), _pick("had", "draw", "平")]}
            if fav == "away" and fav_odds is not None and DRAW_AWAY_PUSH_LO <= fav_odds < DRAW_AWAY_PUSH_HI:
                return {"side": "draw", "mode": "draw_away_fav_push", "fav": fav, "label": "客让1.6~1.8-平",
                        "picks": [_pick("hhad", "home", "让胜"), _pick("had", "away", "负")]}
            if fav == "away" and fav_odds is not None and DRAW_AWAY_DECISIVE_LO <= fav_odds < DRAW_AWAY_DECISIVE_HI:
                return {"side": "draw", "mode": "draw_away_fav_decisive", "fav": fav, "label": "客让2.0~2.3-平",
                        "picks": [_pick("had", "home", "胜"), _pick("had", "away", "负")]}
            if fav == "home" and draw_odds is not None and draw_odds < BALANCED_DRAW_LOW_DRAW:
                return {"side": "draw", "mode": "balanced_draw_home_low", "fav": fav, "label": "主让平赔<3.5-平",
                        "picks": [_pick("hhad", "home", "让胜"), _pick("had", "draw", "平")]}
            if fav == "home" and draw_odds is not None and draw_odds >= BALANCED_DRAW_HIGH_DRAW:
                return {"side": "draw", "mode": "balanced_draw_home_high", "fav": fav, "label": "主让平赔>=4.0-平",
                        "picks": [_pick("hhad", "home", "让胜"), _pick("hhad", "draw", "让平")]}
            return {"side": "draw", "mode": "balanced_draw", "fav": fav, "label": "均衡盘-平",
                    "picks": [_pick("had", "draw", "平"), _pick("hhad", "draw", "让平")]}
        if fav == "home":
            return {"side": "draw", "mode": "deep_draw_home", "fav": fav, "label": "主让<1.5-平",
                    "picks": [_pick("hhad", "home", "让胜"), _pick("hhad", "draw", "让平")]}
        return {"side": "draw", "mode": "deep_draw_away", "fav": fav, "label": "客让<1.5-平",
                "picks": [_pick("hhad", "away", "让负"), _pick("hhad", "draw", "让平")]}

    if best_side == "home":
        if fav == "away":
            if fav_odds is not None and fav_odds < UPSET_WIN_DEEP_ODDS:
                return {"side": "home", "mode": "upset_win_deep", "fav": fav, "label": "客让<1.5-胜",
                        "picks": [_pick("hhad", "draw", "让平"), _pick("hhad", "away", "让负")]}
            if fav_odds is not None and UPSET_WIN_DRAW_LOSS_LO <= fav_odds < UPSET_WIN_DRAW_LOSS_HI:
                return {"side": "home", "mode": "upset_win_draw_loss", "fav": fav, "label": "客让2.0~2.3-胜",
                        "picks": [_pick("had", "draw", "平"), _pick("had", "away", "负")]}
            if fav_odds is not None and fav_odds < UPSET_WIN_DRAW_LOSS_LO:
                if draw_odds is not None and draw_odds >= UPSET_WIN_HIGH_DRAW:
                    return {"side": "home", "mode": "upset_win_mid_highdraw", "fav": fav, "label": "客让1.5~2.0平赔≥4.0-胜",
                            "picks": [_pick("hhad", "home", "让胜")]}
                return {"side": "home", "mode": "upset_win_mid_lowdraw", "fav": fav, "label": "客让1.5~2.0平赔3.5~4.0-胜",
                        "picks": [_pick("hhad", "away", "让负"), _pick("had", "draw", "平")]}
            if fav_odds is not None and fav_odds < UPSET_WIN_MID_HI:
                if draw_odds is not None and draw_odds < UPSET_WIN_LOW_DRAW:
                    return {"side": "home", "mode": "upset_win_mid_low", "fav": fav, "label": "客让2.3~2.6平赔<3.5-胜",
                            "picks": [_pick("had", "draw", "平"), _pick("had", "away", "负")]}
                return {"side": "home", "mode": "upset_win_mid_push", "fav": fav, "label": "客让2.3~2.6平赔3.5~4.0-胜",
                        "picks": [_pick("had", "draw", "平"), _pick("hhad", "draw", "让平")]}
            return {"side": "home", "mode": "upset_win_high_draw", "fav": fav, "label": "客让≥2.6-胜",
                    "picks": [_pick("had", "draw", "平"), _pick("had", "away", "负")]}
        if fav_odds is not None and FAV_WIN_DRAW_LO <= fav_odds < FAV_WIN_DRAW_HI:
            return {"side": "home", "mode": "fav_win_draw", "fav": fav, "label": "主让2.0~2.3-胜",
                    "picks": [_pick("had", "home", "胜"), _pick("had", "draw", "平")]}
        if fav_odds is not None and fav_odds >= FAV_NO_WIN_LO:
            if draw_odds is not None and draw_odds < FAV_NO_WIN_DRAW_SPLIT:
                return {"side": "home", "mode": "fav_no_win_draw_loss", "fav": fav, "label": "主让≥2.6-胜",
                        "picks": [_pick("had", "draw", "平"), _pick("had", "away", "负")]}
            return {"side": "home", "mode": "fav_no_win_loss", "fav": fav, "label": "主让≥2.6-胜",
                    "picks": [_pick("had", "away", "负")]}
        return {"side": "home", "mode": "favorite_win", "fav": fav, "label": "主让-胜",
                "picks": [_pick("had", "home", "胜")]}

    if fav == "home":
        if fav_odds is not None and fav_odds < UPSET_LOSS_DEEP_ODDS:
            return {"side": "away", "mode": "upset_loss_deep", "fav": fav, "label": "主让<1.4-负",
                    "picks": [_pick("hhad", "home", "让胜"), _pick("hhad", "away", "让负")]}
        if fav_odds is not None and FAV_COVER_LO <= fav_odds < FAV_COVER_HI:
            return {"side": "away", "mode": "fav_cover", "fav": fav, "label": "主让1.4~1.6-负",
                    "picks": [_pick("hhad", "home", "让胜")]}
        if fav_odds is not None and FAV_PUSH_LO <= fav_odds < FAV_PUSH_HI:
            return {"side": "away", "mode": "fav_push_loss", "fav": fav, "label": "主让1.6~1.8-负",
                    "picks": [_pick("hhad", "draw", "让平"), _pick("had", "away", "负")]}
        if fav_odds is not None and UPSET_LOSS_MID_LO <= fav_odds < UPSET_LOSS_MID_HI:
            return {"side": "away", "mode": "upset_loss_mid", "fav": fav, "label": "主让1.8~2.0-负",
                    "picks": [_pick("had", "home", "胜"), _pick("had", "draw", "平")]}
        if fav_odds is not None and fav_odds >= FAV_DRAW_LO:
            return {"side": "away", "mode": "fav_push_draw", "fav": fav, "label": "主让≥2.0-负",
                    "picks": [_pick("hhad", "draw", "让平"), _pick("had", "draw", "平")]}
        return {"side": "away", "mode": "upset_loss", "fav": fav, "label": "主让-负",
                "picks": [_pick("hhad", "away", "让负")]}
    if fav_odds is not None and fav_odds < FAVORITE_LOSS_DEEP_ODDS:
        return {"side": "away", "mode": "favorite_loss_deep", "fav": fav, "label": "客让<1.5-负",
                "picks": [_pick("hhad", "draw", "让平"), _pick("hhad", "away", "让负")]}
    if fav_odds is not None and FAVORITE_LOSS_DRAW_HOME_LO <= fav_odds < FAVORITE_LOSS_DRAW_HOME_HI:
        return {"side": "away", "mode": "favorite_loss_draw_home", "fav": fav, "label": "客让2.3~2.6-负",
                "picks": [_pick("had", "home", "胜"), _pick("had", "draw", "平")]}
    return {"side": "away", "mode": "favorite_loss", "fav": fav, "label": "客让-负",
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


def hit_picks(picks, diff, line):
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


def new_picks(label, draw_odds):
    """按规则 + 平赔返回新提取 picks（(market, outcome) 列表）。"""
    if label == "客让-负":
        if draw_odds < 4.0:
            return [("hhad", "home"), ("hhad", "draw")]      # 让胜 + 让平
        return [("hhad", "away"), ("hhad", "home")]          # 让负 + 让胜
    if label == "主让1.4~1.6-负":
        if draw_odds < 4.5:
            return [("had", "home")]                          # 胜
        if draw_odds <= 5.0:
            return [("hhad", "home"), ("hhad", "draw")]      # 让胜 + 让平
        return [("hhad", "away")]                            # 让负
    return None


def rule_name(label, draw_odds):
    if label == "客让-负":
        return "客让+平赔<4.0-负" if draw_odds < 4.0 else "客让+平赔>=4.0-负"
    if label == "主让1.4~1.6-负":
        if draw_odds < 4.5:
            return "主让1.4~1.6+平赔<4.5"
        if draw_odds <= 5.0:
            return "主让1.4~1.6+平赔4.5~5.0"
        return "主让1.4~1.6+平赔>5.0"
    return label


def picks_name(picks):
    H = {"home": "主胜", "draw": "平", "away": "客胜"}
    HH = {"home": "让胜", "draw": "让平", "away": "让负"}
    return " + ".join(HH[o] if m == "hhad" else H[o] for m, o in picks)


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    mrows = await conn.fetch(MATCH_SQL)
    mids = [r["id"] for r in mrows]
    snap_map, line_map = {}, {}
    open_pinnacle, open_bet365 = {}, {}
    if mids:
        for r in await conn.fetch(SNAP_SQL, mids):
            snap_map[r["match_id"]] = _load_json(r["analysis_json"])
        for r in await conn.fetch(LINE_SQL, mids):
            line_map[r["match_id"]] = r["hhad_line"]
        for r in await conn.fetch(OPEN_PINNACLE_SQL, mids):
            open_pinnacle[r["match_id"]] = {"home": float(r["home_win"]), "draw": float(r["draw"]), "away": float(r["away_win"])}
        for r in await conn.fetch(OPEN_BET365_SQL, mids):
            open_bet365[r["match_id"]] = {"home": float(r["home_win"]), "draw": float(r["draw"]), "away": float(r["away_win"])}
    await conn.close()

    open_sharp = dict(open_bet365)
    open_sharp.update(open_pinnacle)

    seen = []
    for r in mrows:
        if r["matchday"] not in seen:
            seen.append(r["matchday"])
        if len(seen) >= LAST_MATCHDAYS:
            break
    target = set(seen)

    # 子规则 -> [n, hit]
    stats = defaultdict(lambda: [0, 0])
    for r in mrows:
        if r["matchday"] not in target:
            continue
        a = snap_map.get(r["id"])
        if not isinstance(a, dict):
            continue
        m1 = (a.get("markets") or {}).get("1x2") or {}
        best_side = (m1.get("value") or {}).get("best_side")
        handicap = (a.get("markets") or {}).get("handicap") or {}
        has_hhad = bool(handicap.get("jczq_odds"))
        if best_side not in ("home", "draw", "away"):
            continue
        open_odds = open_sharp.get(r["id"])
        if not open_odds:
            continue
        pred = extract_prediction(best_side, open_odds, has_hhad)
        if not pred or pred["label"] not in ("客让-负", "主让1.4~1.6-负"):
            continue
        label = pred["label"]
        draw_odds = _to_f(open_odds.get("draw"))
        if draw_odds is None:
            continue
        picks = new_picks(label, draw_odds)
        if not picks:
            continue
        diff = r["home_score"] - r["away_score"]
        line = line_map.get(r["id"])
        hit = hit_picks([{"market": m, "outcome": o, "label": ""} for m, o in picks], diff, line)
        if hit is None:
            continue
        name = rule_name(label, draw_odds)
        stats[name][0] += 1
        if hit:
            stats[name][1] += 1

    print(f"价值分析 · 初盘口径 · 平赔拆分 命中验证（近 {LAST_MATCHDAYS} 比赛日）")
    print("=" * 80)
    order = ["客让+平赔<4.0-负", "客让+平赔>=4.0-负",
             "主让1.4~1.6+平赔<4.5", "主让1.4~1.6+平赔4.5~5.0", "主让1.4~1.6+平赔>5.0"]
    total_n = total_h = 0
    for name in order:
        n, h = stats.get(name, [0, 0])
        total_n += n
        total_h += h
        pname = {
            "客让+平赔<4.0-负": "让胜 + 让平",
            "客让+平赔>=4.0-负": "让负 + 让胜",
            "主让1.4~1.6+平赔<4.5": "胜",
            "主让1.4~1.6+平赔4.5~5.0": "让胜 + 让平",
            "主让1.4~1.6+平赔>5.0": "让负",
        }[name]
        if n == 0:
            print(f"{name:28s}  n=0")
            continue
        print(f"{name:28s}  n={n:2d}  提取[{pname}]  命中 {h}/{n} = {h/n*100:.1f}%")
    print("-" * 80)
    print(f"{'合计':28s}  n={total_n:2d}  命中 {total_h}/{total_n} = {total_h/total_n*100:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
