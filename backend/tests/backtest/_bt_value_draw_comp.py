"""价值分析 · 「平+让平」(均衡盘平) 的赛果构成分析（锐利盘口径，全部规则）。

命中 = HAD平(diff=0) 或 HHAD让平(diff+line=0)。
输出：HAD 赛果分布 / HHAD 赛果分布 / 命中与未中的具体构成。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_draw_comp.py [比赛日数]
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
DECISIVE_LO = 1.80
DECISIVE_HI = 2.00

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


def _load_json(v):
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def _p(n, d):
    return f"{n}/{d}={n/d*100:.1f}%" if d else "—"


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
        m1 = (a.get("markets") or {}).get("1x2") or {}
        best_side = (m1.get("value") or {}).get("best_side")
        sharp = m1.get("sharp_odds") or {}
        handicap = (a.get("markets") or {}).get("handicap") or {}
        has_hhad = bool(handicap.get("jczq_odds"))
        if best_side != "draw" or not has_hhad:
            continue
        fav = _fav_from_1x2(sharp)
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        # 均衡盘平：让球方赔率 >= DEEP_FAV_ODDS 且不在分出胜负区间
        if not (fav_odds is None or fav_odds >= DEEP_FAV_ODDS):
            continue
        if fav is not None and fav_odds is not None and DECISIVE_LO <= fav_odds < DECISIVE_HI:
            continue

        diff = r["home_score"] - r["away_score"]
        line = _to_f(line_map.get(r["id"]))
        had = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        hhad = None
        if line is not None:
            adj = diff + line
            hhad = "home" if adj > 0 else ("draw" if adj == 0 else "away")
        # 命中 = HAD平 或 HHAD让平
        hit = (had == "draw") or (hhad == "draw")

        recs.append({
            "match_num": r["match_num"], "home": r["home_name"], "away": r["away_name"],
            "score": f"{r['home_score']}-{r['away_score']}", "diff": diff,
            "had": had, "hhad": hhad, "hit": hit,
        })

    n = len(recs)
    hit = sum(1 for x in recs if x["hit"])
    print(f"平+让平（均衡盘平） n={n}，命中 {hit}={hit/n*100 if n else 0:.1f}%（锐利盘口径，近{LAST_MATCHDAYS}比赛日）")
    print("=" * 80)

    # HAD 赛果分布
    had_dist = defaultdict(int)
    for x in recs:
        had_dist[x["had"]] += 1
    print("HAD 实际赛果：", {("主胜" if k=="home" else ("平" if k=="draw" else "客胜")): v for k, v in had_dist.items()})

    # HHAD 赛果分布
    hhad_dist = defaultdict(int)
    for x in recs:
        k = x["hhad"]
        if k is None:
            hhad_dist["无线"] += 1
        else:
            hhad_dist[{"home":"让胜","draw":"让平","away":"让负"}[k]] += 1
    print("HHAD 实际赛果：", dict(hhad_dist))

    # 命中构成
    hit_draw = sum(1 for x in recs if x["had"] == "draw")           # 平局命中
    hit_hhad = sum(1 for x in recs if x["hhad"] == "draw")          # 让球平命中
    both = sum(1 for x in recs if x["had"] == "draw" and x["hhad"] == "draw")
    print(f"\n命中构成：平局 {hit_draw} 场 · 让球平 {hit_hhad} 场 · 两者重合 {both} 场")

    # 未中构成：按 HAD 方向
    miss = [x for x in recs if not x["hit"]]
    miss_home = sum(1 for x in miss if x["had"] == "home")
    miss_away = sum(1 for x in miss if x["had"] == "away")
    miss_draw = sum(1 for x in miss if x["had"] == "draw")
    print(f"未中构成（按HAD）：主胜 {miss_home} · 平 {miss_draw} · 客胜 {miss_away}")

    # 未中按 HHAD 方向
    miss_hhad = defaultdict(int)
    for x in miss:
        k = x["hhad"]
        miss_hhad[{"home":"让胜","draw":"让平","away":"让负"}.get(k, "无线")] += 1
    print(f"未中构成（按HHAD）：{dict(miss_hhad)}")


if __name__ == "__main__":
    asyncio.run(main())
