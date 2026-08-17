"""检查 08-15 / 08-16 比赛周期的近期交锋(H2H)与近期状态(form)数据
比赛周期定义：竞彩 12:00 ~ 次日 12:00（北京时间 naive）
"""
import asyncio
import asyncpg
import json
from datetime import datetime, timedelta

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

SQL_MATCHES = """
SELECT m.id, m.jc_match_id, m.match_num, m.kickoff_time, m.status,
       m.sportmonks_fixture_id AS fx, m.home_team_name AS hraw, m.away_team_name AS araw,
       l.name_zh AS lg,
       th.id AS hid, th.name_zh AS hzh, th.name_en AS hen, th.sportmonks_id AS hsm,
       ta.id AS aid, ta.name_zh AS azh, ta.name_en AS aen, ta.sportmonks_id AS asm
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN teams th ON th.id = m.home_team_id
LEFT JOIN teams ta ON ta.id = m.away_team_id
WHERE m.kickoff_time >= $1 AND m.kickoff_time < $2
ORDER BY m.match_num, m.kickoff_time
"""


async def main():
    conn = await asyncpg.connect(DSN)

    for day in ["2026-08-15", "2026-08-16"]:
        print(f"\n{'='*100}")
        print(f"=== {day} 比赛周期（{day} 12:00 ~ 次日 12:00）===")
        print(f"{'='*100}")
        start = datetime.strptime(f"{day} 12:00:00", "%Y-%m-%d %H:%M:%S")
        end = start + timedelta(hours=24)
        rows = await conn.fetch(SQL_MATCHES, start, end)

        if not rows:
            print("  （无比赛）")
            continue

        for r in rows:
            hid, aid = r["hid"], r["aid"]
            # ── H2H：双方对位（含方向交换） ──
            h2h_rows = await conn.fetch(
                """
                SELECT h.id, h.match_date, h.competition, h.home_team_id, h.away_team_id,
                       h.home_score, h.away_score, h.sportmonks_fixture_id AS fx
                FROM head_to_head h
                WHERE (h.home_team_id = $1 AND h.away_team_id = $2)
                   OR (h.home_team_id = $2 AND h.away_team_id = $1)
                ORDER BY h.match_date DESC
                """,
                hid, aid,
            )
            # ── 近期状态：team_season_stats.recent_matches ──
            stats_rows = await conn.fetch(
                """
                SELECT s.team_id, s.season, s.league_id, s.played, s.form, s.recent_matches
                FROM team_season_stats s
                WHERE s.team_id = ANY($1::int[])
                """,
                [hid, aid],
            )
            stat_map = {s["team_id"]: s for s in stats_rows}

            def _recent_info(team_id):
                s = stat_map.get(team_id)
                if not s:
                    return None, None, "无统计记录"
                rm = s["recent_matches"]
                if not rm:
                    return s, None, f"recent_matches 空(played={s['played']})"
                if isinstance(rm, str):
                    rm = json.loads(rm)
                if not isinstance(rm, list) or len(rm) == 0:
                    return s, None, f"recent_matches 空列表(played={s['played']})"
                last = rm[-1]
                latest = last.get("date") if isinstance(last, dict) else last
                if latest is None:
                    return s, None, f"recent_matches 最后一条无 date(共{len(rm)}条)"
                return s, latest, None

            h_info, h_latest, h_err = _recent_info(hid)
            a_info, a_latest, a_err = _recent_info(aid)

            issues = []
            if len(h2h_rows) == 0:
                issues.append("【缺H2H】")
            h2h_latest = max((h["match_date"] for h in h2h_rows), default=None)

            for side, err in [("主", h_err), ("客", a_err)]:
                if err:
                    issues.append(f"【{side}状态:{err}】")

            print(f"\n  #{r['id']} {r['match_num']} [{r['lg']}] {r['status']} ko={r['kickoff_time']}")
            print(f"    主: {r['hzh'] or r['hen']}(id={hid},sm={r['hsm']})  |  客: {r['azh'] or r['aen']}(id={aid},sm={r['asm']})  fx={r['fx']}")
            print(f"    H2H: {len(h2h_rows)} 条" + (f" 最新={h2h_latest}" if h2h_latest else " <<< 无交锋"))
            if h2h_rows:
                for h in h2h_rows[:6]:
                    hname = "主" if h["home_team_id"] == hid else "客"
                    aname = "客" if h["away_team_id"] == aid else "主"
                    print(f"      - {h['match_date'].date()} [{h['competition'] or ''}] {h['home_team_id']}({hname}) {h['home_score']}:{h['away_score']} {h['away_team_id']}({aname}) fx={h['fx']}")
            for side, s, latest in [("主", h_info, h_latest), ("客", a_info, a_latest)]:
                if s:
                    n_recent = len(s["recent_matches"]) if isinstance(s["recent_matches"], list) else "?"
                    print(f"    {side}队状态: season={s['season']} played={s['played']} form={s['form']} recent={n_recent}条 最新={latest}")
                else:
                    print(f"    {side}队状态: {h_err if side=='主' else a_err}")
            if issues:
                print(f"    {' '.join(issues)}")

    await conn.close()


asyncio.run(main())
