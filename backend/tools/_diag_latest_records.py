"""查看 flagged 球队的 stats 记录明细：确认 UI 读取的 latest 记录是否为空"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

TEAMS = [301, 520, 157, 158, 340, 326, 402, 426, 181, 1590, 245, 153, 248, 176]


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """SELECT team_id, season, played, form,
                  CASE WHEN recent_matches IS NULL THEN 0 ELSE json_array_length(recent_matches::json) END AS n_recent
           FROM team_season_stats WHERE team_id = ANY($1::int[])
           ORDER BY team_id, season DESC""",
        TEAMS,
    )
    for r in rows:
        print(f"T{r['team_id']}: season={r['season']!r:>12} played={r['played']} form={r['form']!r} recent={r['n_recent']}条")
    await conn.close()


asyncio.run(main())
