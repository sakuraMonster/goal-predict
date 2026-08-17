"""查 1202 引用的比赛，判断哪条记录是正确 Alverca"""
import asyncio
import asyncpg
import json

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, l.name_zh AS lg, m.kickoff_time,
               th.name_zh AS hzh, ta.name_zh AS azh
        FROM matches m
        LEFT JOIN leagues l ON l.id = m.league_id
        LEFT JOIN teams th ON th.id = m.home_team_id
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE m.home_team_id=1202 OR m.away_team_id=1202
        ORDER BY m.kickoff_time DESC LIMIT 20
        """
    )
    out = [json.dumps([dict(r, kickoff_time=str(r["kickoff_time"])) for r in rows], ensure_ascii=True)]
    open("_out1202.txt", "w", encoding="utf-8").write("\n".join(out))
    await conn.close()


asyncio.run(main())
