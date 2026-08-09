"""修复比赛缺失的 league_id，并同步更新 predictions"""
import asyncio
import asyncpg

# 手动映射: match_id → league_id
FIXES = {
    15523: 18,  # 坎布尔 vs SBV精英 → 荷乙
    15524: 18,  # 奥斯 vs 布雷达 → 荷乙
    15525: 18,  # 埃门 vs 罗达JC → 荷乙
    15526: 19,  # 波鸿 vs 柏林赫塔 → 德乙
    15527: 22,  # 米德尔斯堡 vs 雷克斯汉姆 → 英冠
    15528: 21,  # 埃斯托里尔 vs 法马利康 → 葡超
}

async def main():
    c = await asyncpg.connect(
        "postgresql://postgres:postgres@localhost:5432/football_prediction"
    )

    for match_id, lg_id in FIXES.items():
        # 更新 matches
        await c.execute(
            "UPDATE matches SET league_id = $1 WHERE id = $2", lg_id, match_id
        )
        # 更新 predictions 的冗余 league_id
        await c.execute(
            "UPDATE predictions SET league_id = $1 WHERE match_id = $2", lg_id, match_id
        )

    print(f"已更新 {len(FIXES)} 场比赛的 league_id (matches + predictions)")

    # 验证
    rows = await c.fetch("""
        SELECT m.id, m.match_num, m.home_team_name, m.away_team_name,
               l.name_zh AS lg
        FROM matches m
        JOIN leagues l ON m.league_id = l.id
        WHERE m.id = ANY($1::int[])
    """, list(FIXES.keys()))
    print("\n验证结果:")
    for r in rows:
        print(f"  match_id={r['id']}  {r['match_num']}  {r['home_team_name']} vs {r['away_team_name']}  → {r['lg']}")

    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
