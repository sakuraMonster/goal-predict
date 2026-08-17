"""快速确认 1608 合并状态 + 08-15/16 周期 H2H/状态缺口"""
import asyncio
import asyncpg
import json
from datetime import datetime, timedelta

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 1. team 1608 合并状态 ===")
    r = await conn.fetchrow("SELECT id FROM teams WHERE id=1608")
    print(f"  1608 exists: {r is not None}")
    rows = await conn.fetch("SELECT id, home_team_id, away_team_id, sportmonks_fixture_id AS fx FROM matches WHERE id IN (15567,15652)")
    for r in rows:
        print(f"  match #{r['id']}: {r['home_team_id']} vs {r['away_team_id']} fx={r['fx']}")
    r = await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE id=1590")
    print(f"  1590: zh={r['name_zh']} en={r['name_en']} sm={r['sportmonks_id']}")

    print("\n=== 2. 08-15/08-16 周期比赛 H2H + 状态缺口 ===")
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
        print(f"\n--- {day} 周期（{len(rows)} 场）---")
        for r in rows:
            hid, aid = r["hid"], r["aid"]
            h2h_rows = await conn.fetch(
                """SELECT id, match_date FROM head_to_head
                   WHERE (home_team_id=$1 AND away_team_id=$2) OR (home_team_id=$2 AND away_team_id=$1)
                   ORDER BY match_date DESC""", hid, aid,
            )
            h2h_latest = max((h["match_date"] for h in h2h_rows), default=None)
            stats_rows = await conn.fetch(
                """SELECT team_id, season, played, form, recent_matches FROM team_season_stats WHERE team_id=ANY($1::int[])""",
                [hid, aid],
            )
            info = []
            for s in stats_rows:
                rm = s["recent_matches"]
                n = len(json.loads(rm)) if isinstance(rm, str) and rm else 0
                info.append(f"T{s['team_id']}: played={s['played']} recent={n}条")
            flag = []
            if len(h2h_rows) == 0:
                flag.append("缺H2H")
            if not info:
                flag.append("无统计")
            else:
                for s in stats_rows:
                    if not s["recent_matches"]:
                        flag.append(f"T{s['team_id']}状态空")
            status = " ".join(flag) if flag else "OK"
            print(f"  #{r['id']} [{r['lg']}] {r['hzh']}({hid},sm={r['hsm']}) vs {r['azh']}({aid},sm={r['asm']}) "
                  f"fx={r['fx']} H2H={len(h2h_rows)}条最新{h2h_latest} | {'; '.join(info)} | {status}")

    await conn.close()


asyncio.run(main())
