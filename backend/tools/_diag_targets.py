"""精确判断补数目标：
1. 每队顶级 stats 记录（UI 实际读取 season DESC LIMIT 1）是否缺 recent_matches/form
2. 每场 H2H 是否有记录
3. 双方 sm_id 是否齐全（决定能否补）
"""
import asyncio
import asyncpg
from datetime import datetime, timedelta

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    for day in ["2026-08-15", "2026-08-16"]:
        start = datetime.strptime(f"{day} 12:00:00", "%Y-%m-%d %H:%M:%S")
        end = start + timedelta(hours=24)
        rows = await conn.fetch(
            """
            SELECT m.id, m.match_num, m.kickoff_time, m.status, m.sportmonks_fixture_id AS fx,
                   l.name_zh AS lg, th.id AS hid, th.name_zh AS hzh, th.sportmonks_id AS hsm,
                   ta.id AS aid, ta.name_zh AS azh, ta.sportmonks_id AS asm
            FROM matches m
            LEFT JOIN leagues l ON l.id=m.league_id
            LEFT JOIN teams th ON th.id=m.home_team_id
            LEFT JOIN teams ta ON ta.id=m.away_team_id
            WHERE m.kickoff_time >= $1 AND m.kickoff_time < $2
            ORDER BY m.kickoff_time
            """, start, end,
        )
        print(f"\n===== {day} 周期（{len(rows)} 场）=====")
        for r in rows:
            hid, aid = r["hid"], r["aid"]
            # 顶级记录 per team
            top = {}
            for tid in (hid, aid):
                if tid is None:
                    top[tid] = None
                    continue
                rec = await conn.fetchrow(
                    """SELECT season, played, form,
                              CASE WHEN recent_matches IS NULL THEN 0 ELSE json_array_length(recent_matches::json) END AS n_recent
                       FROM team_season_stats WHERE team_id=$1
                       ORDER BY season DESC LIMIT 1""", tid,
                )
                top[tid] = rec
            h2h_n = await conn.fetchval(
                """SELECT COUNT(*) FROM head_to_head
                   WHERE (home_team_id=$1 AND away_team_id=$2) OR (home_team_id=$2 AND away_team_id=$1)""",
                hid, aid,
            ) if hid and aid else 0
            h2h_n = h2h_n or 0

            probs = []
            for side, tid in [("主", hid), ("客", aid)]:
                rec = top.get(tid)
                if rec is None:
                    probs.append(f"{side}队无记录")
                elif rec["n_recent"] == 0:
                    probs.append(f"{side}队顶记录缺recent(season={rec['season']})")
            if h2h_n == 0:
                probs.append("缺H2H")
            sm_ok = bool(r["hsm"] and r["asm"])
            if not sm_ok:
                probs.append(f"SM不齐(h={r['hsm']},a={r['asm']})")
            status = "| ".join(probs) if probs else "OK"
            print(f"  #{r['id']} [{r['lg']}] {r['hzh']} vs {r['azh']} fx={r['fx']} | {status}")

    await conn.close()


asyncio.run(main())
