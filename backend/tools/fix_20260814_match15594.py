"""2026-08-14 #15594 球队映射 + 联赛映射修复

修复内容：
1. 艾卜哈(1714) → SM 5618 (Abha)
2. 拉斯决心(1715) 合并到已存在 1215 (Al Hazm, sm=17694)
3. #15594 补 sportmonks_fixture_id=19777726
4. 沙职(id=26) 补 sportmonks_id=944
5. Al Hazm(1215) 的 league_id 从 6(韩K) 修正为 26(沙职)

SM 实测依据：
- fixture 19777726 = Abha(5618) vs Al Hazm(17694)，UTC 08-13 16:15 = 本地 08-14 00:15，吻合 #15594
- 相邻 fixture 19777725 = #15595（利雅得青年 vs 胡巴尔卡德西亚）
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)

    # ── 预检 ──
    assert await conn.fetchval("SELECT count(*) FROM teams WHERE sportmonks_id=5618") == 0, "SM 5618 已被占用"
    assert await conn.fetchval("SELECT count(*) FROM teams WHERE id=1215 AND sportmonks_id=17694") == 1, "1215 非 Al Hazm"
    assert await conn.fetchval("SELECT count(*) FROM leagues WHERE sportmonks_id=944") == 0, "SM league 944 已被占用"
    cnt1715 = await conn.fetchval(
        "SELECT count(*) FROM matches WHERE home_team_id=1715 OR away_team_id=1715")
    assert cnt1715 == 1, f"1715 引用数异常 cnt={cnt1715}"
    assert await conn.fetchval("SELECT count(*) FROM matches WHERE id=15594 AND away_team_id=1715") == 1, "#15594 away 非 1715"
    print("预检通过", flush=True)

    async with conn.transaction():
        # ── 1) 联赛映射：沙职 id=26 → SM 944 ──
        await conn.execute("UPDATE leagues SET sportmonks_id=944 WHERE id=26")

        # ── 2) 艾卜哈(1714) → Abha(5618) ──
        await conn.execute(
            "UPDATE teams SET sportmonks_id=5618, name_en='Abha', short_en='ABH', "
            "league_id=26, needs_review=false, review_reason=NULL WHERE id=1714")
        for alias, src, primary in (("艾卜哈", "sporttery.cn", True), ("Abha", "sportmonks", False)):
            n = await conn.fetchval(
                "SELECT count(*) FROM team_aliases WHERE team_id=1714 AND alias_name=$1", alias)
            if n == 0:
                await conn.execute(
                    "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) "
                    "VALUES (1714, $1, $2, $3)", alias, src, primary)
        print("艾卜哈 1714 → Abha(5618)", flush=True)

        # ── 3) 拉斯决心(1715) 合并到 1215 (Al Hazm) ──
        await conn.execute("UPDATE matches SET away_team_id=1215 WHERE id=15594 AND away_team_id=1715")
        await conn.execute(
            "UPDATE teams SET name_zh='拉斯决心', short_en='HAZ', league_id=26 WHERE id=1215")
        for alias, src, primary in (("拉斯决心", "sporttery.cn", True), ("Al Hazm", "sportmonks", False)):
            n = await conn.fetchval(
                "SELECT count(*) FROM team_aliases WHERE team_id=1215 AND alias_name=$1", alias)
            if n == 0:
                await conn.execute(
                    "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) "
                    "VALUES (1215, $1, $2, $3)", alias, src, primary)
        print("拉斯决心 1715 → 1215 (Al Hazm)", flush=True)

        # ── 4) 删除占位 1715 ──
        await conn.execute("DELETE FROM team_aliases WHERE team_id=1715")
        await conn.execute("DELETE FROM team_season_stats WHERE team_id=1715")
        await conn.execute("DELETE FROM head_to_head WHERE home_team_id=1715 OR away_team_id=1715")
        await conn.execute("DELETE FROM injuries WHERE team_id=1715")
        n = await conn.execute("DELETE FROM teams WHERE id=1715")
        print(f"删除占位 1715 rows={n}", flush=True)

        # ── 5) #15594 补 fixture ──
        await conn.execute("UPDATE matches SET sportmonks_fixture_id=19777726 WHERE id=15594")

    # ── 复验 ──
    rows = await conn.fetch(
        """SELECT m.id, m.sportmonks_fixture_id AS fx, l.name_zh AS lg, l.sportmonks_id AS lsm,
                  th.id AS hid, th.name_zh AS hzh, th.name_en AS hen, th.sportmonks_id AS hsm,
                  ta.id AS aid, ta.name_zh AS azh, ta.name_en AS aen, ta.sportmonks_id AS asm
           FROM matches m
           JOIN teams th ON th.id=m.home_team_id
           JOIN teams ta ON ta.id=m.away_team_id
           JOIN leagues l ON l.id=m.league_id
           WHERE m.id=15594""")
    for r in rows:
        print("VERIFY", dict(r), flush=True)

    dup = await conn.fetch(
        "SELECT sportmonks_id, count(*) c FROM teams WHERE sportmonks_id IS NOT NULL "
        "GROUP BY sportmonks_id HAVING count(*) > 1")
    print("SM_DUPS:", [dict(d) for d in dup], flush=True)

    assert await conn.fetchval("SELECT count(*) FROM teams WHERE id=1715") == 0, "1715 未删除"
    print("ALL DONE", flush=True)
    await conn.close()


asyncio.run(main())
