# -*- coding: utf-8 -*-
"""今日(09-09)方案F串：halfdraw R1 半平腿 × 方案D当日方向腿。"""
import asyncio
import json
import sys

sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

import asyncpg
from app.api import market_flow as mf
from app.db.database import async_session

CN2KEY = {"胜胜": "HH", "平胜": "DH", "负胜": "AH", "胜平": "HD", "平平": "DD",
          "负平": "AD", "胜负": "HA", "平负": "DA", "负负": "AA"}


def _lds(js):
    if isinstance(js, str):
        try:
            return json.loads(js)
        except Exception:
            return None
    return js


async def main():
    # 1) 当日 D 方向腿（生产引擎）
    async with async_session() as db:
        res = await mf.market_flow_parlay_d_recommend(date="2026-09-09", db=db)
    out = []
    out.append("== 方案D 引擎输出 2026-09-09 ==")
    picks = res.get("picks") or []
    for p in picks:
        out.append(json.dumps(p, ensure_ascii=False, default=str))
    # 2) R1 腿芝加哥场 HAFU 赔率
    conn = await asyncpg.connect("postgresql://postgres:postgres@localhost:5432/football_prediction")
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.home_team_name, m.away_team_name, j.hafu_odds_json,
               j.had_home, j.had_draw, j.had_away, j.hhad_line
        FROM jczq_play_odds_snapshots j JOIN matches m ON m.id = j.match_id
        WHERE m.match_num IN ('周三015','周三001','周三013') AND m.kickoff_time >= '2026-09-09'
        ORDER BY j.snapshot_time DESC
        """)
    await conn.close()
    seen = set()
    for r in rows:
        if r["match_num"] in seen:
            continue
        seen.add(r["match_num"])
        js = _lds(r["hafu_odds_json"]) or {}
        out.append(f"\n== {r['match_num']} {r['home_team_name']} vs {r['away_team_name']} ==")
        out.append(f"HAD 主{js.get('hh')} ... HAFU 9格: {json.dumps(js, ensure_ascii=False)}")
    with open(r"e:\zhangxuejun\new-thinking\ricking-03\backend\tools\_today_parlayf_out.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("done")


asyncio.run(main())
