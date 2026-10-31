"""价值分析 · 预测提取 · DEEP_FAV_ODDS 阈值敏感性测算（近 N 个比赛日）。

目的：DEEP_FAV_ODDS 只影响「平」预测的均衡盘/深盘分档，据此扫描不同阈值下的命中率，
      判断当前 1.50 是否需要调整。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_pred_sweep.py
"""
import sys
import os
import asyncio
import json

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7

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


def _hit_picks(picks, diff, line):
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


SIDE_PICKS = {
    "home": [{"market": "had", "outcome": "home", "label": "胜"}],
    "away": [{"market": "had", "outcome": "away", "label": "负"}],
}


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
        md = r["matchday"]
        if md not in seen:
            seen.append(md)
        if len(seen) >= LAST_MATCHDAYS:
            break
    target = set(seen)

    draw_rows = []   # {fav_odds, hit_balanced, hit_deep}
    non_draw_hit = 0
    non_draw_n = 0

    for r in mrows:
        if r["matchday"] not in target:
            continue
        analysis = snap_map.get(r["id"])
        if not isinstance(analysis, dict):
            continue
        m1 = (analysis.get("markets") or {}).get("1x2") or {}
        best_side = (m1.get("value") or {}).get("best_side")
        had_odds = m1.get("sharp_odds")
        handicap = (analysis.get("markets") or {}).get("handicap") or {}
        has_hhad = bool(handicap.get("jczq_odds"))
        if best_side not in ("home", "draw", "away"):
            continue

        diff = r["home_score"] - r["away_score"]
        line = line_map.get(r["id"])
        fav = _fav_from_had(had_odds)
        fav_odds = _to_f((had_odds or {}).get(fav)) if fav else None

        if best_side == "draw":
            # 均衡盘分支：平 + 让平
            picks_bal = [
                {"market": "had", "outcome": "draw", "label": "平"},
                {"market": "hhad", "outcome": "draw", "label": "让平"},
            ]
            # 深盘分支：让球方负 + 让球方平
            loss_outcome = "away" if fav == "home" else "home"
            picks_deep = [
                {"market": "hhad", "outcome": loss_outcome, "label": "让负" if fav == "home" else "让胜"},
                {"market": "hhad", "outcome": "draw", "label": "让平"},
            ]
            hit_bal = _hit_picks(picks_bal, diff, line)
            hit_deep = _hit_picks(picks_deep, diff, line)
            draw_rows.append({
                "fav": fav,
                "fav_odds": fav_odds,
                "hit_balanced": hit_bal,
                "hit_deep": hit_deep,
            })
        else:
            # 胜/负：DEEP_FAV_ODDS 不影响，按正路/冷门定单选项
            if best_side == "home":
                picks = [{"market": "hhad", "outcome": "home", "label": "让胜"}] if fav == "away" else SIDE_PICKS["home"]
            else:
                picks = [{"market": "hhad", "outcome": "away", "label": "让负"}] if fav == "home" else SIDE_PICKS["away"]
            h = _hit_picks(picks, diff, line)
            non_draw_n += 1
            if h:
                non_draw_hit += 1

    draw_n = len(draw_rows)

    # 两分支独立命中率 + fav_odds 分布（帮助理解阈值该往哪调）
    bal_hit = sum(1 for d in draw_rows if d["hit_balanced"])
    deep_hit = sum(1 for d in draw_rows if d["hit_deep"])
    print("=" * 100)
    print(f"平预测场次 draw_n={draw_n} 两分支独立命中率：")
    print(f"  均衡盘分支(平 + 让平)       : {bal_hit}/{draw_n} = {bal_hit/draw_n*100 if draw_n else 0:.1f}%")
    print(f"  深盘分支(让球方负 + 让球方平): {deep_hit}/{draw_n} = {deep_hit/draw_n*100 if draw_n else 0:.1f}%")
    buckets = {"无让球方": 0, "<1.5": 0, "1.5~1.75": 0, "1.75~2.0": 0, ">=2.0": 0}
    for d in draw_rows:
        fo = d["fav_odds"]
        if fo is None:
            buckets["无让球方"] += 1
        elif fo < 1.5:
            buckets["<1.5"] += 1
        elif fo < 1.75:
            buckets["1.5~1.75"] += 1
        elif fo < 2.0:
            buckets["1.75~2.0"] += 1
        else:
            buckets[">=2.0"] += 1
    print(f"  平预测场次的让球方胜赔分布: {buckets}")
    print("=" * 100)

    print("=" * 100)
    print(f"DEEP_FAV_ODDS 阈值敏感性测算   (近 {LAST_MATCHDAYS} 个比赛日)")
    print(f"平预测场次 draw_n={draw_n} | 非平(胜/负)场次 non_draw_n={non_draw_n} 命中 {non_draw_hit} ({non_draw_hit/non_draw_n*100 if non_draw_n else 0:.1f}%)")
    print("=" * 100)
    print(f"{'阈值T':>6} {'均衡盘':>4} {'深盘':>4} {'平命中':>5} {'平命中率':>8} {'总命中':>6} {'总命中率':>8}")
    print("-" * 100)

    def _choose(hit_bal, hit_deep, fav_odds, T):
        # fav_odds 为 None（无让球方）恒走均衡盘
        if fav_odds is None:
            return hit_bal
        return hit_deep if fav_odds < T else hit_bal

    best = None
    for T in [round(1.20 + 0.05 * i, 2) for i in range(29)]:  # 1.20 ~ 2.60
        bal_n = 0
        deep_n = 0
        draw_hit = 0
        for d in draw_rows:
            if d["fav_odds"] is None or d["fav_odds"] >= T:
                bal_n += 1
                h = d["hit_balanced"]
            else:
                deep_n += 1
                h = d["hit_deep"]
            if h:
                draw_hit += 1
        total_hit = non_draw_hit + draw_hit
        total_n = non_draw_n + draw_n
        draw_rate = draw_hit / draw_n if draw_n else 0.0
        total_rate = total_hit / total_n if total_n else 0.0
        mark = "  <- 当前" if abs(T - 1.50) < 1e-9 else ""
        print(f"{T:6.2f} {bal_n:4d} {deep_n:4d} {draw_hit:5d} {draw_rate*100:7.1f}% {total_hit:6d} {total_rate*100:7.1f}%{mark}")
        if best is None or total_hit > best[1]:
            best = (T, total_hit, total_n, total_rate)

    print("-" * 100)
    print(f"总命中最高：T={best[0]} 命中 {best[1]}/{best[2]} = {best[3]*100:.1f}%")
    print("注：仅凭近7天样本(平预测 n=%d)选阈值易过拟合；此处仅为敏感性参考。" % draw_n)


if __name__ == "__main__":
    asyncio.run(main())
