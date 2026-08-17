"""检查团队 1608 (利勒斯特罗姆) 引用 + sm=2510 是否存在"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    print("=== team 1608 / sm=2510 / sm=269 ===")
    rows = await conn.fetch(
        """
        SELECT id, name_zh, name_en, sportmonks_id, needs_review, review_reason,
               created_at
        FROM teams
        WHERE id = 1608 OR sportmonks_id IN (2510, 269)
        ORDER BY id
        """
    )
    for r in rows:
        print(f"  id={r['id']} name_zh={r['name_zh']} name_en={r['name_en']} sm={r['sportmonks_id']} nr={r['needs_review']} reason={r['review_reason']} created={r['created_at']}")

    print("\n=== 引用 1608 的比赛 ===")
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, m.kickoff_time, m.status, m.sportmonks_fixture_id,
               l.name_zh AS lg,
               th.name_zh AS h, ta.name_zh AS a
        FROM matches m
        LEFT JOIN leagues l ON l.id=m.league_id
        LEFT JOIN teams th ON th.id=m.home_team_id
        LEFT JOIN teams ta ON ta.id=m.away_team_id
        WHERE m.home_team_id=1608 OR m.away_team_id=1608
        ORDER BY m.kickoff_time
        """
    )
    for r in rows:
        print(f"  #{r['id']} {r['match_num']} [{r['lg']}] {r['h']} vs {r['a']} ko={r['kickoff_time']} status={r['status']} fx={r['sportmonks_fixture_id']}")

    print("\n=== 引用 sm=269 被错误映射的其它记录 ===")
    rows = await conn.fetch(
        """
        SELECT h.id, h.match_date, h.competition, h.home_team_id, h.away_team_id, h.home_score, h.away_score
        FROM head_to_head h
        WHERE h.home_team_id=1608 OR h.away_team_id=1608
        ORDER BY h.match_date DESC LIMIT 10
        """
    )
    for r in rows:
        print(f"  h2h#{r['id']} {r['match_date'].date()} [{r['competition']}] {r['home_team_id']} {r['home_score']}:{r['away_score']} {r['away_team_id']}")

    print("\n=== 1608 的 team_season_stats ===")
    rows = await conn.fetch(
        """
        SELECT id, season, played, wins, draws, losses, goals_for, goals_against, form
        FROM team_season_stats WHERE team_id=1608
        """
    )
    for r in rows:
        print(f"  #{r['id']} season={r['season']} played={r['played']} w/d/l={r['wins']}/{r['draws']}/{r['losses']} gf/ga={r['goals_for']}/{r['goals_against']} form={r['form']}")

    print("\n=== 15652 fixture 19629600 验证（SM 主客）===")
    await conn.close()


asyncio.run(main())
