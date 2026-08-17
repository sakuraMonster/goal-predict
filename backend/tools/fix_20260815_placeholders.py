"""08-15 竞彩新增 13 支占位球队修复（SM API 实测前置验证）

背景：sync_matches 为 13 支球队创建占位（1749-1761）。实查发现全部 13 支在
teams 表已有正确现存记录（SM 实测 sm_id 全部一致），仅 name_zh 缺失/为英文名
（如 Al Kholood=439、Al Ittihad=464）导致竞彩网中文名精确匹配失败而新建占位。

修复：
1. 8 场 match 引用从占位改回现存球队 + 补齐 4 场缺失 fx（SM 实测）+ is_swapped 全 False
2. 13 支现存球队补 name_zh 中文名 + sporttery 中文主别名 + 缩写别名（防再次创建占位）
3. 删除 13 个冗余占位（先迁别名再删）
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

# 占位 -> 现存映射: (占位id, 现存id, 现存name_zh, 中文别名, 缩写别名)
MERGES = [
    (1749, 340, "博尔顿", "博尔顿", "BOL"),          # Bolton Wanderers sm=16
    (1750, 326, "普雷斯顿", "普雷斯顿", "PRE"),       # Preston North End sm=99
    (1751, 320, "诺维奇", "诺维奇", "NOH"),          # Norwich City sm=33
    (1752, 325, "西布罗姆维奇", "西布罗姆维奇", "WEB"),  # West Bromwich Albion sm=10
    (1753, 458, "布赖代合作", "布赖代合作", "ATW"),    # Al Taawoun sm=2392
    (1754, 435, "赛哈特海湾", "赛哈特海湾", "AKJ"),    # Al Khaleej sm=12216
    (1755, 368, "谢菲尔德联", "谢菲尔德联", "SHE"),    # Sheffield United sm=21
    (1756, 464, "吉达联合", "吉达联合", "AIT"),       # Al Ittihad sm=476
    (1757, 439, "拉斯永恒", "拉斯永恒", "AOO"),       # Al Kholood sm=232744（现存，无中文名）
    (1758, 436, "利雅得胜利", "利雅得胜利", "ANR"),    # Al Nassr sm=2506
    (1759, 432, "穆拜赖兹征服", "穆拜赖兹征服", "ALF"), # Al Fateh sm=5891
    (1760, 296, "伯恩利", "伯恩利", "BUR"),          # Burnley sm=27
    (1761, 317, "加的夫城", "加的夫城", "CAR"),       # Cardiff City sm=69
]

# 比赛引用修复: match_id -> (home, away, fx, is_swapped)
MATCH_FIXES = [
    (15649, 340, 326, 19729165, False),  # 博尔顿vs普雷斯顿 fx 已有，SM 验证正确
    (15651, 320, 325, 19729161, False),  # 诺维奇vs西布朗 fx 补齐
    (15654, 458, 435, 19777720, False),  # 布赖代合作vs赛哈特海湾 fx 补齐
    (15655, 368, 344, 19729158, False),  # 谢菲尔德联vs伯明翰 fx 已有
    (15656, 464, 439, 19777719, False),  # 吉达联合vs拉斯永恒(→439 Al Kholood) fx 补齐
    (15657, 436, 432, 19777718, False),  # 利雅得胜利vs穆拜赖兹征服 fx 补齐
    (15667, 296, 295, 19729156, False),  # 伯恩利vsWestHam fx 已有
    (15678, 317, 328, 19729155, False),  # 加的夫城vs雷克斯汉姆 fx 已有
]

# 待删除占位（13 个全部删除）
DELETE_IDS = [1749, 1750, 1751, 1752, 1753, 1754, 1755, 1756, 1757, 1758, 1759, 1760, 1761]

# 现存球队预期 sm_id（断言用）
EXPECT_SM = {340: 16, 326: 99, 320: 33, 325: 10, 458: 2392, 435: 12216,
             368: 21, 464: 476, 436: 2506, 432: 5891, 296: 27, 317: 69, 439: 232744}


async def main():
    conn = await asyncpg.connect(DSN)

    # ── 前置断言 ──
    # 1. 现存球队 sm_id 正确
    for tid, sm_id in EXPECT_SM.items():
        row = await conn.fetchrow("SELECT sportmonks_id FROM teams WHERE id=$1", tid)
        assert row and row["sportmonks_id"] == sm_id, f"现存 {tid} sm_id 断言失败: {row}"
    # 2. 8 场比赛当前引用占位
    cur = await conn.fetch(
        "SELECT id, home_team_id, away_team_id FROM matches WHERE id=ANY($1::int[])",
        [m[0] for m in MATCH_FIXES],
    )
    cur_map = {r["id"]: (r["home_team_id"], r["away_team_id"]) for r in cur}
    expected_refs = {15649: (1749, 1750), 15651: (1751, 1752), 15654: (1753, 1754),
                     15655: (1755, 344), 15656: (1756, 1757), 15657: (1758, 1759),
                     15667: (1760, 295), 15678: (1761, 328)}
    for mid, (h, a) in expected_refs.items():
        assert cur_map.get(mid) == (h, a), f"match {mid} 当前引用 {cur_map.get(mid)} != 预期 {(h, a)}"
    # 3. 1757 仍为占位（无 sm_id，待删除）
    r = await conn.fetchrow("SELECT sportmonks_id FROM teams WHERE id=1757")
    assert r["sportmonks_id"] is None, "1757 已有 sm_id，应先查归属再决定合并策略"
    # 4. 现存球队无中文别名冲突（排除占位自身持有的缩写别名）
    dup = await conn.fetch(
        "SELECT team_id, alias_name FROM team_aliases "
        "WHERE alias_name=ANY($1::text[]) AND NOT (team_id = ANY($2::int[]))",
        [m[3] for m in MERGES] + [m[4] for m in MERGES],
        [m[0] for m in MERGES],
    )
    assert not dup, f"别名冲突: {dup}"

    print("前置断言全部通过")

    async with conn.transaction():
        # 1. 迁移占位缩写别名 → 现存球队
        for pid, tid, *_ in MERGES:
            await conn.execute(
                "UPDATE team_aliases SET team_id=$1 WHERE team_id=$2", tid, pid
            )
        print("[1] 缩写别名迁移完成")

        # 2. 新增中文名主别名
        for _, tid, _, zh, abbr in MERGES:
            await conn.execute(
                "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) VALUES ($1,$2,'sporttery.cn',TRUE)",
                tid, zh,
            )
        print("[2] 中文主别名插入完成")

        # 3. 现存球队补 name_zh（原为英文名）
        for _, tid, zh, *_ in MERGES:
            await conn.execute("UPDATE teams SET name_zh=$1 WHERE id=$2", zh, tid)
        print("[3] 现存球队 name_zh 补齐完成")

        # 4. match 引用 + fx + is_swapped
        for mid, h, a, fx, sw in MATCH_FIXES:
            await conn.execute(
                "UPDATE matches SET home_team_id=$1, away_team_id=$2, sportmonks_fixture_id=$3, is_swapped=$4 WHERE id=$5",
                h, a, fx, sw, mid,
            )
        print("[5] match 引用修复完成")

        # 5. 删除冗余占位（别名已迁移，无其他依赖）
        for pid in DELETE_IDS:
            await conn.execute("DELETE FROM teams WHERE id=$1", pid)
        print(f"[5] 删除占位 {len(DELETE_IDS)} 个完成")

    await conn.close()
    print("\n=== 修复完成 ===")


asyncio.run(main())
