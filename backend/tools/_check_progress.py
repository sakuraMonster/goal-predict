"""查看 sync 进行中的写入情况：1590 stats、缺 H2H 对位是否已出现记录"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_progress.txt"


async def main():
    conn = await asyncpg.connect(DSN)
    lines = []

    def p(s=""):
        lines.append(str(s))
        print(s)

    p("=== 1590 (Lillestrøm) team_season_stats ===")
    rows = await conn.fetch(
        "SELECT id, season, league_id, played, form FROM team_season_stats WHERE team_id=1590"
    )
    for r in rows:
        p(f"  #{r['id']} season={r['season']} lg={r['league_id']} played={r['played']} form={r['form']}")

    p("\n=== 各目标场次 H2H 现状 ===")
    targets = [
        (15622, "秋田vs富山"),
        (15650, "米亚尔比vs天狼星"),
        (15637, "德岛vs鸟栖"),
        (15663, "奥勒松vs瓦勒伦加"),
        (15665, "卡尔马vs哈马比"),
        (15652, "KFUM vs 利勒斯特罗姆"),
    ]
    for mid, label in targets:
        r = await conn.fetchrow("SELECT home_team_id, away_team_id FROM matches WHERE id=$1", mid)
        if not r:
            p(f"  #{mid} {label}: 比赛不存在")
            continue
        h, a = r["home_team_id"], r["away_team_id"]
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM head_to_head WHERE (home_team_id=$1 AND away_team_id=$2) OR (home_team_id=$2 AND away_team_id=$1)",
            h, a,
        )
        latest = await conn.fetchval(
            "SELECT MAX(match_date) FROM head_to_head WHERE (home_team_id=$1 AND away_team_id=$2) OR (home_team_id=$2 AND away_team_id=$1)",
            h, a,
        )
        p(f"  #{mid} {label}: H2H={cnt} 条, 最新={latest}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    p(f"\n[written] {OUT}")
    await conn.close()


asyncio.run(main())
