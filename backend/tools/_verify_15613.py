"""核实 15613 特尔斯达 vs 鹿特丹斯巴达 的球队映射与联赛归属"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 15613 的 home/away team_id 与 teams 记录 ===")
    rows = await conn.fetch(
        """
        SELECT m.id, m.home_team_id, m.away_team_id,
               th.name_zh AS hzh, th.name_en AS hen, th.sportmonks_id AS hsm,
               ta.name_zh AS azh, ta.name_en AS aen, ta.sportmonks_id AS asm,
               th.league_id AS hl, ta.league_id AS al
        FROM matches m
        LEFT JOIN teams th ON th.id = m.home_team_id
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.id = 15613
        """
    )
    for r in rows:
        print(f"  home_team_id={r['home_team_id']} [{r['hzh']}|{r['hen']}|sm={r['hsm']}|league_id={r['hl']}]")
        print(f"  away_team_id={r['away_team_id']} [{r['azh']}|{r['aen']}|sm={r['asm']}|league_id={r['al']}]")

    print("\n=== 查 sportmonks_id=1550 与 919 的 team ===")
    rows = await conn.fetch(
        """
        SELECT id, name_zh, name_en, sportmonks_id, league_id
        FROM teams WHERE sportmonks_id IN (1550, 919)
        """
    )
    for r in rows:
        print(f"  team={r['id']} [{r['name_zh']}|{r['name_en']}] sm={r['sportmonks_id']} league_id={r['league_id']}")

    print("\n=== 特尔斯达/鹿特丹斯巴达相关球队 ===")
    rows = await conn.fetch(
        """
        SELECT id, name_zh, name_en, sportmonks_id, league_id
        FROM teams
        WHERE name_zh LIKE '%特尔斯达%' OR name_zh LIKE '%鹿特丹斯巴达%'
           OR name_en ILIKE '%telstar%' OR name_en ILIKE '%sparta%'
        """
    )
    for r in rows:
        print(f"  team={r['id']} [{r['name_zh']}|{r['name_en']}] sm={r['sportmonks_id']} league_id={r['league_id']}")

    print("\n=== 15613 的 fixture（SM 侧主客与球队）===")
    rows = await conn.fetch(
        """
        SELECT id, sportmonks_fixture_id, league_id, kickoff_time, venue
        FROM matches WHERE id = 15613
        """
    )
    print(f"  fx={rows[0]['sportmonks_fixture_id']} league_id={rows[0]['league_id']} venue={rows[0]['venue']}")

    await conn.close()


asyncio.run(main())
