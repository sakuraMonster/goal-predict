"""价值分析 · 「主让-负」(upset_loss) 与「客让-负」(favorite_loss) 规则下具体场次清单（锐利盘口径）。

主让-负 = 冷门负（主队让球但价值分析优选客胜，且不在 1.4~1.6/1.6~1.8/≥2.0 细分区间）→ 提取「让负」。
客让-负 = 正路负（客队让球，价值分析优选客胜）→ 提取「负」。

命中口径：
  主让-负：实际让球胜平负 == 让负。
  客让-负：实际胜平负 == 客胜(负)。

运行：
  docker exec -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/bt_loss_list.py [比赛日数]
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
        mode = pred.get("mode")
        if mode not in ("upset_loss", "favorite_loss"):
            continue
        m1 = (a.get("markets") or {}).get("1x2") or {}
        sharp = m1.get("sharp_odds") or {}
        fav = pred.get("fav")
        fav_odds = _to_f(sharp.get(fav)) if fav else None
        hs, aw = r["home_score"], r["away_score"]
        diff = hs - aw
        had = "home" if diff > 0 else ("draw" if diff == 0 else "away")
        line = _to_f(line_map.get(r["id"]))
        hhad = None
        if line is not None:
            adj = diff + line
            hhad = "home" if adj > 0 else ("draw" if adj == 0 else "away")
        # 命中：upset_loss → 让负；favorite_loss → 客胜(负)
        if mode == "upset_loss":
            hit = (hhad == "away")
        else:
            hit = (had == "away")
        recs.append({
            "mode": mode,
            "match_num": r["match_num"],
            "league": r["league"],
            "home": r["home_name"],
            "away": r["away_name"],
            "kickoff": str(r["kickoff_time"])[:16],
            "fav": fav,
            "fav_odds": fav_odds,
            "score": f"{hs}-{aw}",
            "had": had,
            "hhad": hhad,
            "hit": hit,
        })

    HAD_ZH = {"home": "主胜", "draw": "平", "away": "客胜"}
    HHAD_ZH = {"home": "让胜", "draw": "让平", "away": "让负"}

    for mode, title in [("upset_loss", "主让-负（冷门负 → 让负）"), ("favorite_loss", "客让-负（正路负 → 负）")]:
        sub = [x for x in recs if x["mode"] == mode]
        n = len(sub)
        hit_n = sum(1 for x in sub if x["hit"])
        print(f"【{title}】mode={mode}")
        print(f"近 {LAST_MATCHDAYS} 比赛日：n={n}，命中 {hit_n}，命中率 {hit_n/n*100:.1f}%\n")
        for i, x in enumerate(sub, 1):
            flag = "命中" if x["hit"] else "未中"
            fav_zh = "主让" if x["fav"] == "home" else "客让"
            print(
                f"{i:2d}. [{x['match_num']}] {x['league']} {x['home']} vs {x['away']} "
                f"({x['kickoff']}) | {fav_zh}赔 {x['fav_odds']} | 比分 {x['score']} "
                f"{HAD_ZH[x['had']]}/{HHAD_ZH[x['hhad']]} | {flag}"
            )
        print()


if __name__ == "__main__":
    asyncio.run(main())
