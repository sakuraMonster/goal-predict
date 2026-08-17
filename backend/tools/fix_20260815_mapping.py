"""08-15 比赛周期球队映射修复（SM API 实测前置验证）

修复项：
1. 15629 阿尔维卡: home 402 → 1202(正确记录 sm=35867 Alverca)，fx=19736847；402 标记 needs_review 留作遗留(与1202重复,sm=269225 AVS错误)
2. 15633 SBV精英: home 346 → 444(正确 sm=1652 Excelsior)，fx=19714688；346 释放 sm=185(Exeter City 错误) 置 NULL + needs_review
3. 15627 海登海姆: away 1746 → 301(正确 sm=2831)，fx=19735492，is_swapped=True；删占位 1746
4. 15622 秋田蓝色闪电: home 1744 → 1697(sm=18263)，away 1745 → 1698(sm=17798)，fx=19719468，is_swapped=True；删占位 1744/1745
5. 15637 德岛漩涡: home 1747 → 1694(sm=5402)，away 1748 → 1686(sm=639)，fx=19719462，is_swapped=True；删占位 1747/1748
6. 15644 葡萄牙国民: home 785 → 485(sm=7035)，away 1635 → 427(sm=1198)，fx=19736842，is_swapped=False；删错误记录 1635
7. 15643 阿森纳vs曼城: is_swapped False → True（fx=19713619 中 ManCity=home）
8. 补中文名: 1686→鸟栖沙岩, 1694→德岛漩涡, 485→葡萄牙国民
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    async def q(sql, *args):
        return await conn.execute(sql, *args)

    # 1. 阿尔维卡 15629 → 1202
    await q("UPDATE matches SET home_team_id=1202, sportmonks_fixture_id=19736847, is_swapped=FALSE WHERE id=15629")
    await q("UPDATE teams SET needs_review=TRUE, review_reason='重复记录：sm=269225(AVS)错误映射，正确记录为1202(sm=35867 Alverca)，待合并' WHERE id=402")
    print("[1] 15629 home→1202(sm=35867 Alverca) fx=19736847; 402 标记 needs_review 留作遗留")

    # 2. SBV精英 15633 → 444
    await q("UPDATE matches SET home_team_id=444, sportmonks_fixture_id=19714688, is_swapped=FALSE WHERE id=15633")
    await q("UPDATE teams SET sportmonks_id=NULL, name_en='UNKNOWN:SBV精英(重复,合并至444)', "
            "needs_review=TRUE, review_reason='重复记录：sm原为185(Exeter City)错误映射，已合并至444(sm=1652 Excelsior)' WHERE id=346")
    print("[2] 15633 home→444(sm=1652 Excelsior) fx=19714688; 346 释放 sm=185 置 NULL + needs_review")

    # 3. 海登海姆 15627 → 301
    await q("UPDATE matches SET away_team_id=301, sportmonks_fixture_id=19735492, is_swapped=TRUE WHERE id=15627")
    await q("DELETE FROM team_aliases WHERE team_id=1746")
    await q("DELETE FROM teams WHERE id=1746")
    print("[3] 15627 away→301(sm=2831) fx=19735492 swapped=True; 删占位 1746")

    # 4. 秋田/富山 15622 → 1697/1698
    await q("UPDATE matches SET home_team_id=1697, away_team_id=1698, sportmonks_fixture_id=19719468, is_swapped=TRUE WHERE id=15622")
    for tid in (1744, 1745):
        await q("DELETE FROM team_aliases WHERE team_id=$1", tid)
        await q("DELETE FROM teams WHERE id=$1", tid)
    print("[4] 15622 home→1697(sm=18263) away→1698(sm=17798) fx=19719468 swapped=True; 删占位 1744/1745")

    # 5. 德岛/鸟栖 15637 → 1694/1686
    await q("UPDATE matches SET home_team_id=1694, away_team_id=1686, sportmonks_fixture_id=19719462, is_swapped=TRUE WHERE id=15637")
    for tid in (1747, 1748):
        await q("DELETE FROM team_aliases WHERE team_id=$1", tid)
        await q("DELETE FROM teams WHERE id=$1", tid)
    print("[5] 15637 home→1694(sm=5402) away→1686(sm=639) fx=19719462 swapped=True; 删占位 1747/1748")

    # 6. 葡萄牙国民/埃斯托里尔 15644 → 485/427
    await q("UPDATE matches SET home_team_id=485, away_team_id=427, sportmonks_fixture_id=19736842, is_swapped=FALSE WHERE id=15644")
    await q("DELETE FROM team_aliases WHERE team_id=1635")
    await q("DELETE FROM team_season_stats WHERE team_id=1635")
    await q("DELETE FROM teams WHERE id=1635")
    print("[6] 15644 home→485(sm=7035) away→427(sm=1198) fx=19736842 swapped=False; 删错误记录 1635")

    # 7. 15643 阿森纳 vs 曼城 swapped 修正
    await q("UPDATE matches SET is_swapped=TRUE WHERE id=15643")
    print("[7] 15643 is_swapped False→True (fx=19713619 ManCity=home)")

    # 8. 补中文名
    await q("UPDATE teams SET name_zh='鸟栖沙岩' WHERE id=1686")
    await q("UPDATE teams SET name_zh='德岛漩涡' WHERE id=1694")
    await q("UPDATE teams SET name_zh='葡萄牙国民' WHERE id=485")
    print("[8] 补中文名: 1686=鸟栖沙岩, 1694=德岛漩涡, 485=葡萄牙国民")

    await conn.close()
    print("\n=== 修复完成 ===")


asyncio.run(main())
