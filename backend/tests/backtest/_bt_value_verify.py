"""验证用户观察：均衡盘「平+让平」中，让球方锐利盘赔率 1.8~2.0 时的实际赛果分布。

观察1：客队为让球方(fav=away)且客队锐利盘赔率 1.8~2.0 → 平+让平很少中，反而主胜(冷门胜)或客胜2+(让负)。
观察2：主队为让球方(fav=home)且主队锐利盘赔率 1.8~2.0 → 平+让平很少中，反而客胜(冷门负)或主胜2+(让胜)。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_verify.py [比赛日数] [阈值T]
"""
import sys
import os
import asyncio
import json
from collections import defaultdict

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
T = float(sys.argv[2]) if len(sys.argv) > 2 else 1.50
LO, HI = 1.80, 2.00

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
        if not (fav_odds is None or fav_odds >= T):
            continue  # 均衡盘

        diff = r["home_score"] - r["away_score"]
        line = _to_f(line_map.get(r["id"]))
        hhad_actual = None
        if line is not None:
            adj = diff + line
            hhad_actual = "home" if adj > 0 else ("draw" if adj == 0 else "away")
        had_actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        any_hit = (had_actual == "draw") or (hhad_actual == "draw")

        recs.append({
            "fav": fav, "fav_odds": fav_odds, "diff": diff, "line": line,
            "had": had_actual, "hhad": hhad_actual, "any": any_hit,
        })

    def report(cond, title):
        sub = [x for x in recs if cond(x)]
        n = len(sub)
        if not n:
            print(f"{title}: n=0")
            return
        hit = sum(1 for x in sub if x["any"])
        print(f"\n{title}  n={n}  平+让平命中 {hit}/{n}={hit/n*100:.1f}%")
        # 净胜球分布
        dist = defaultdict(int)
        for x in sub:
            d = x["diff"]
            if d <= -2:
                k = "客净胜2+"
            elif d == -1:
                k = "客净胜1"
            elif d == 0:
                k = "平局"
            elif d == 1:
                k = "主净胜1"
            else:
                k = "主净胜2+"
            dist[k] += 1
        for k in ["主净胜2+", "主净胜1", "平局", "客净胜1", "客净胜2+"]:
            if dist.get(k):
                print(f"    {k}: {dist[k]}")
        # hhad 分布
        hh = defaultdict(int)
        for x in sub:
            hh[x["hhad"] or "无线"] += 1
        print(f"    让球结果(让胜/让平/让负): {dict(hh)}")

    # 观察1：客队让球方 + 客队锐利盘赔率 1.8~2.0
    report(lambda x: x["fav"] == "away" and (x["fav_odds"] is not None and LO <= x["fav_odds"] < HI),
           f"观察1：客队让球方且客队锐利盘赔率[{LO},{HI})")
    # 观察2：主队让球方 + 主队锐利盘赔率 1.8~2.0
    report(lambda x: x["fav"] == "home" and (x["fav_odds"] is not None and LO <= x["fav_odds"] < HI),
           f"观察2：主队让球方且主队锐利盘赔率[{LO},{HI})")

    # 对照：其余均衡盘（同区间外的所有均衡盘）
    report(lambda x: not ((x["fav"] in ("home", "away")) and (x["fav_odds"] is not None and LO <= x["fav_odds"] < HI)),
           "对照：其余均衡盘（不含 1.8~2.0 让球方）")

    # 更细的 fav_odds 分档 × fav 主客（看是否 1.8~2.0 特别低）
    print("\n按 fav_odds 分档 × 让球方主客 的「平+让平」命中率：")
    buckets = [(1.5, 1.8), (1.8, 2.0), (2.0, 2.3), (2.3, 2.6), (2.6, 99)]
    for side in ("home", "away"):
        for lo, hi in buckets:
            sub = [x for x in recs if x["fav"] == side and (x["fav_odds"] is not None and lo <= x["fav_odds"] < hi)]
            if sub:
                h = sum(1 for x in sub if x["any"])
                print(f"  {('主让' if side=='home' else '客让')} fav[{lo},{hi}): n={len(sub)} 命中 {h}/{len(sub)}={h/len(sub)*100:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
