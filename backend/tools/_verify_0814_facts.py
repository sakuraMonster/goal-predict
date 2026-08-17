"""验证：Model B 口径命中率 / Model D 入库情况 / 15613 联赛归属"""
import asyncio
import asyncpg
import json

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== 08-14 周期：Model B(全局 snap_top2) vs Model C(snap_top2_c) 进球判定对比 ===")
    rows = await conn.fetch(
        """
        SELECT p.match_id, p.snap_top2, p.snap_top2_c, p.actual_total_goals, p.result_goals, p.model_version
        FROM predictions p
        JOIN matches m ON m.id = p.match_id
        WHERE m.kickoff_time >= '2026-08-14 12:00:00' AND m.kickoff_time < '2026-08-15 12:00:00'
        ORDER BY m.kickoff_time
        """
    )
    b_hit = c_hit = 0
    for r in rows:
        act = r["actual_total_goals"]
        if act is None:
            continue
        sb = json.loads(r["snap_top2"]) if isinstance(r["snap_top2"], str) else (r["snap_top2"] or [])
        sc = json.loads(r["snap_top2_c"]) if isinstance(r["snap_top2_c"], str) else (r["snap_top2_c"] or [])
        cap = min(act, 4)
        bh = act in sb or cap in sb
        ch = cap in sc
        if bh:
            b_hit += 1
        if ch:
            c_hit += 1
        mark = "B✓" if bh else "B✗"
        mark += " C✓" if ch else " C✗"
        print(f"  match={r['match_id']} 实际={act} SNAP_B={sb} SNAP_C={sc} result_goals={r['result_goals']} [{mark}] v={r['model_version']}")
    print(f"\n  Model B(全局) 命中: {b_hit}/{len(rows)}={b_hit/len(rows)*100:.1f}%")
    print(f"  Model C 命中: {c_hit}/{len(rows)}={c_hit/len(rows)*100:.1f}%")

    print("\n=== Model D 字段入库情况（全库非空计数）===")
    row = await conn.fetchrow(
        """
        SELECT count(*) FILTER (WHERE expected_goals_d IS NOT NULL) AS eg_d,
               count(*) FILTER (WHERE snap_top2_d IS NOT NULL) AS snap_d,
               count(*) AS total
        FROM predictions
        """
    )
    print(f"  expected_goals_d 非空={row['eg_d']}  snap_top2_d 非空={row['snap_d']}  总记录={row['total']}")

    print("\n=== 15613 球队与联赛 ===")
    rows = await conn.fetch(
        """
        SELECT t.id, t.name_zh, t.name_en, t.sportmonks_id, tl.name_zh AS team_league
        FROM teams t LEFT JOIN leagues tl ON tl.id = t.league_id
        WHERE t.id IN (1550, 919)
        """
    )
    for r in rows:
        print(f"  team={r['id']} {r['name_zh']} {r['name_en']} sm={r['sportmonks_id']} team.league={r['team_league']}")
    row = await conn.fetchrow(
        """
        SELECT m.id, m.match_num, l.name_zh AS match_league, l.id AS league_id,
               (SELECT name_zh FROM leagues WHERE id = 24) AS lid24_name
        FROM matches m LEFT JOIN leagues l ON l.id = m.league_id WHERE m.id = 15613
        """
    )
    print(f"  match={row['id']} match_num={row['match_num']} match.league={row['match_league']}(id={row['league_id']})")
    print(f"  荷乙 id=24 名称={row['lid24_name']}")

    print("\n=== 荷乙/荷甲 在库球队抽查（15613 两队的真实归属）===")
    rows = await conn.fetch(
        """
        SELECT COUNT(*) FILTER (WHERE name_zh ILIKE '%特尔斯达%') AS telstar,
               COUNT(*) FILTER (WHERE name_zh ILIKE '%鹿特丹斯巴达%' OR name_en ILIKE '%Sparta%Rotterdam%') AS sparta
        FROM teams
        """
    )
    print(f"  telstar 队数={rows[0]['telstar']}  sparta 队数={rows[0]['sparta']}")

    await conn.close()


asyncio.run(main())
