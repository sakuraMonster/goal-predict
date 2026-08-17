"""最终验证：逐场同步后的 H2H 内容 + 缺口清单 + 15652 引用正确性"""
import asyncio
import asyncpg
from datetime import datetime, timedelta

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 1. 之前缺 H2H 场次现状 ===")
    for mid in [15627, 15649, 15631, 15633, 15634, 15638, 15644, 15673, 15663, 15665, 15652]:
        r = await conn.fetchrow(
            """SELECT m.id, l.name_zh AS lg, th.name_zh AS h, ta.name_zh AS a,
                      th.sportmonks_id AS hsm, ta.sportmonks_id AS asm, m.status
               FROM matches m
               LEFT JOIN leagues l ON l.id=m.league_id
               LEFT JOIN teams th ON th.id=m.home_team_id
               LEFT JOIN teams ta ON ta.id=m.away_team_id
               WHERE m.id=$1""", mid,
        )
        h2h = await conn.fetch(
            """SELECT id, match_date, competition, home_team_id, away_team_id,
                      home_score, away_score, sportmonks_fixture_id AS fx
               FROM head_to_head
               WHERE (home_team_id=$1 AND away_team_id=$2) OR (home_team_id=$2 AND away_team_id=$1)
               ORDER BY match_date DESC""", r["h"] and None or None, None,
        ) if False else None
        print(f"  #{mid} [{r['lg']}] {r['h']} vs {r['a']} (sm={r['hsm']}/{r['asm']}) status={r['status']}")

    print("\n=== 2. 15663 奥勒松 vs 瓦勒伦加 H2H 明细 ===")
    rows = await conn.fetch(
        """SELECT h.id, h.match_date, h.competition, h.home_team_id, th.name_zh AS hzh,
                  h.away_team_id, ta.name_zh AS azh, h.home_score, h.away_score, h.sportmonks_fixture_id AS fx
           FROM head_to_head h
           LEFT JOIN teams th ON th.id=h.home_team_id
           LEFT JOIN teams ta ON ta.id=h.away_team_id
           WHERE (h.home_team_id=248 AND h.away_team_id=176) OR (h.home_team_id=176 AND h.away_team_id=248)
           ORDER BY h.match_date DESC""",
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_date'].date()} [{r['competition']}] {r['hzh']}({r['home_team_id']}) {r['home_score']}:{r['away_score']} {r['azh']}({r['away_team_id']}) fx={r['fx']}")

    print("\n=== 3. 15665 卡尔马 vs 哈马比 H2H 明细 ===")
    rows = await conn.fetch(
        """SELECT h.id, h.match_date, h.competition, h.home_team_id, th.name_zh AS hzh,
                  h.away_team_id, ta.name_zh AS azh, h.home_score, h.away_score, h.sportmonks_fixture_id AS fx
           FROM head_to_head h
           LEFT JOIN teams th ON th.id=h.home_team_id
           LEFT JOIN teams ta ON ta.id=h.away_team_id
           WHERE (h.home_team_id=245 AND h.away_team_id=153) OR (h.home_team_id=153 AND h.away_team_id=245)
           ORDER BY h.match_date DESC""",
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_date'].date()} [{r['competition']}] {r['hzh']}({r['home_team_id']}) {r['home_score']}:{r['away_score']} {r['azh']}({r['away_team_id']}) fx={r['fx']}")

    print("\n=== 4. 15652 奥斯陆KFUM vs 利勒斯特罗姆 H2H 明细（引用应为 181/1590）===")
    rows = await conn.fetch(
        """SELECT h.id, h.match_date, h.competition, h.home_team_id, th.name_zh AS hzh, th.sportmonks_id AS hsm,
                  h.away_team_id, ta.name_zh AS azh, ta.sportmonks_id AS asm, h.home_score, h.away_score, h.sportmonks_fixture_id AS fx
           FROM head_to_head h
           LEFT JOIN teams th ON th.id=h.home_team_id
           LEFT JOIN teams ta ON ta.id=h.away_team_id
           WHERE (h.home_team_id=181 AND h.away_team_id=1590) OR (h.home_team_id=1590 AND h.away_team_id=181)
           ORDER BY h.match_date DESC""",
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_date'].date()} [{r['competition']}] {r['hzh']}({r['home_team_id']},sm={r['hsm']}) {r['home_score']}:{r['away_score']} {r['azh']}({r['away_team_id']},sm={r['asm']}) fx={r['fx']}")

    print("\n=== 5. 残留 sm=None 球队（影响 H2H 补齐）===")
    rows = await conn.fetch(
        """SELECT DISTINCT t.id, t.name_zh, t.name_en, t.sportmonks_id
           FROM teams t
           JOIN matches m ON m.home_team_id=t.id OR m.away_team_id=t.id
           WHERE m.kickoff_time >= '2026-08-15 12:00:00' AND m.kickoff_time < '2026-08-17 12:00:00'
             AND t.sportmonks_id IS NULL"""
    )
    for r in rows:
        print(f"  T{r['id']} {r['name_zh'] or r['name_en']} sm={r['sportmonks_id']}")

    await conn.close()


asyncio.run(main())
