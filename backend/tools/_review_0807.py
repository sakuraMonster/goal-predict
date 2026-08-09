"""复盘 08-07 比赛周期 Model C 进球预测（10场完整版）"""
import asyncio
import asyncpg
import json
from datetime import datetime

async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    # 北京时间 08-07 12:00 ~ 08-08 12:00
    start = datetime(2026, 8, 7, 12, 0)
    end = datetime(2026, 8, 8, 12, 0)

    # 用 LEFT JOIN 避免联赛缺失导致丢数据
    rows = await conn.fetch("""
        SELECT p.id, p.expected_goals_c, p.snap_top2_c,
               p.actual_home_score, p.actual_away_score,
               p.actual_total_goals, p.actual_score,
               p.result_goals, p.confidence_level, p.is_cold_match,
               p.over_2_5_prob,
               m.home_team_name, m.away_team_name, m.match_num,
               COALESCE(l.name_zh, '?') AS league_name,
               p.kickoff_time
        FROM predictions p
        JOIN matches m ON p.match_id = m.id
        LEFT JOIN leagues l ON m.league_id = l.id
        WHERE p.kickoff_time >= $1 AND p.kickoff_time < $2
        ORDER BY p.kickoff_time
    """, start, end)

    total = len(rows)
    print(f"当前数据库该时段共 {total} 场比赛")
    print()

    if not rows:
        await conn.close()
        return

    print("=" * 100)
    print("  Model C 进球数预测复盘 | 08-07 比赛周期（北京时间 08-07 12:00 ~ 08-08 12:00）")
    print("=" * 100)
    print()

    settled = 0
    hit = 0
    all_info = []

    for r in rows:
        snap_c = json.loads(r["snap_top2_c"]) if isinstance(r["snap_top2_c"], str) else (r["snap_top2_c"] or [])
        actual = r["actual_total_goals"]
        eg_c = r["expected_goals_c"] or 0
        league = r["league_name"] or "?"
        home = r["home_team_name"] or ""
        away = r["away_team_name"] or ""
        ko = str(r["kickoff_time"])[:16] if r["kickoff_time"] else ""
        actual_score = r["actual_score"] or "?"
        match_num = r["match_num"] or ""
        over = r["over_2_5_prob"]

        if actual is not None:
            settled += 1
            act_cap = min(actual, 4)
            c_hit = act_cap in snap_c
            if c_hit:
                hit += 1
        else:
            c_hit = None

        snap_str = "/".join(str(x) for x in snap_c[:2]) if snap_c else "?"

        info = {
            "match_num": match_num, "league": league,
            "home": home, "away": away, "ko": ko,
            "actual": actual, "score": actual_score,
            "eg_c": eg_c, "snap_str": snap_str,
            "hit": c_hit, "settled": actual is not None,
            "cold": r["is_cold_match"], "over_2_5": over,
        }
        all_info.append(info)

        hit_mark = "✓" if c_hit else ("✗" if c_hit is False else "-")
        cold_tag = "冷门" if r["is_cold_match"] else ""
        print(f"  [{match_num}] {league}  {home} vs {away}")
        print(f"    {ko}  比分:{actual_score}  总进球:{actual if actual is not None else '?'}")
        print(f"    γ={eg_c:.1f}  SNAP={snap_str}  大2.5={over:.0%}  → {hit_mark}  {cold_tag}")
        print()

    # 汇总
    print("-" * 100)
    print(f"  总计: {total}场 | 已结算: {settled}场 | 未结算: {total - settled}场")
    if settled:
        acc = round(hit / settled * 100, 1)
        print(f"  Model C 进球命中: {hit}/{settled} = {acc}%")
        print(f"  近30天均值: 61.1%")

    # 偏差分析
    if settled:
        print()
        print("=" * 100)
        print("  偏差分析：未命中场次")
        print("=" * 100)
        for m in all_info:
            if not m["settled"] or m["hit"] is True:
                continue
            tags = []
            if m["cold"]: tags.append("冷门")
            if m["actual"] >= 5: tags.append("超大球")
            elif m["actual"] >= 4: tags.append("大球")
            elif m["actual"] <= 1: tags.append("小球")
            tag_str = " | ".join(tags) if tags else ""
            dir_str = "↑高估" if m["eg_c"] > m["actual"] else "↓低估"
            print(f"  [{m['league']}] {m['home']} vs {m['away']}  {m['score']} (总{m['actual']}球)")
            print(f"    γ={m['eg_c']:.1f}, SNAP={m['snap_str']}, 偏差={(m['eg_c']-m['actual']):+.1f} {dir_str}, 大2.5={m['over_2_5']:.0%}  {tag_str}")

        # 命中场次
        print()
        print("=" * 100)
        print("  命中场次")
        print("=" * 100)
        for m in all_info:
            if m["settled"] and m["hit"] is True:
                diff = abs(m["eg_c"] - m["actual"])
                print(f"  [{m['league']}] {m['home']} vs {m['away']}  {m['score']} (总{m['actual']}球)  γ={m['eg_c']:.1f}  SNAP={m['snap_str']}  偏差={diff:.1f}")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
