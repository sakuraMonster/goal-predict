"""价值分析 · 预测提取 · 平预测两条分支的逐项命中拆解（锐利盘口径）。

针对「平 + 让平」与「让球方负 + 让球方平」两分支，分别统计每个 pick 的独立命中率，
用于回答「让平」这一项到底命中多少。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_va_pickhit.py [比赛日数] [阈值T]
"""
import sys
import os
import asyncio
import json

sys.path.insert(0, "/app")
import asyncpg

LAST_MATCHDAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
T = float(sys.argv[2]) if len(sys.argv) > 2 else 1.50

MATCH_SQL = """
SELECT m.id, m.match_num, m.kickoff_time, m.home_score, m.away_score,
       ((m.kickoff_time - interval '12 hours')::date)::text AS matchday
FROM matches m
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

    # 均衡盘分支(平+让平)逐项
    bal_n = 0
    bal_ping = 0      # 平(胜平负平局)命中
    bal_rangping = 0  # 让平(让球平)命中
    bal_any = 0       # 任一命中
    # 深盘分支(让球方负+让球方平)逐项
    deep_n = 0
    deep_fanglose = 0  # 让球方负命中
    deep_rangping = 0  # 让球方平命中
    deep_any = 0

    for r in mrows:
        if r["matchday"] not in target:
            continue
        analysis = snap_map.get(r["id"])
        if not isinstance(analysis, dict):
            continue
        m1 = (analysis.get("markets") or {}).get("1x2") or {}
        best_side = (m1.get("value") or {}).get("best_side")
        sharp = m1.get("sharp_odds") or {}
        handicap = (analysis.get("markets") or {}).get("handicap") or {}
        has_hhad = bool(handicap.get("jczq_odds"))
        if best_side != "draw" or not has_hhad:
            continue

        fav = _fav_from_1x2(sharp)
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        diff = r["home_score"] - r["away_score"]
        line = _to_f(line_map.get(r["id"]))

        had_actual = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        hhad_actual = None
        if line is not None:
            adj = diff + line
            hhad_actual = "home" if adj > 0 else ("draw" if adj == 0 else "away")

        ping_hit = (had_actual == "draw")
        rangping_hit = (hhad_actual == "draw") if hhad_actual is not None else None

        is_balanced = (fav_odds is None or fav_odds >= T)
        if is_balanced:
            bal_n += 1
            if ping_hit:
                bal_ping += 1
            if rangping_hit:
                bal_rangping += 1
            if ping_hit or rangping_hit:
                bal_any += 1
        else:
            loss_outcome = "away" if fav == "home" else "home"
            fanglose_hit = (hhad_actual == loss_outcome) if hhad_actual is not None else None
            deep_n += 1
            if fanglose_hit:
                deep_fanglose += 1
            if rangping_hit:
                deep_rangping += 1
            if fanglose_hit or rangping_hit:
                deep_any += 1

    def _p(n, d):
        return f"{n}/{d} = {n/d*100:.1f}%" if d else "n/a"

    print(f"平预测两条分支逐项命中拆解（锐利盘口径，T={T}，近 {LAST_MATCHDAYS} 比赛日）")
    print("=" * 80)
    print(f"[均衡盘分支] 平 + 让平  n={bal_n}")
    print(f"    平(胜平负平局)  命中 {_p(bal_ping, bal_n)}")
    print(f"    让平(让球平)    命中 {_p(bal_rangping, bal_n)}")
    print(f"    任一命中          {_p(bal_any, bal_n)}")
    print("-" * 80)
    print(f"[深盘分支] 让球方负 + 让球方平  n={deep_n}")
    print(f"    让球方负         命中 {_p(deep_fanglose, deep_n)}")
    print(f"    让球方平(让平)   命中 {_p(deep_rangping, deep_n)}")
    print(f"    任一命中          {_p(deep_any, deep_n)}")
    print("-" * 80)
    tot_rangping = bal_rangping + deep_rangping
    tot_draw = bal_n + deep_n
    print(f"平预测全体(n={tot_draw})中「让平」这一项合计命中 {_p(tot_rangping, tot_draw)}")


if __name__ == "__main__":
    asyncio.run(main())
