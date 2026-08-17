"""2026-08-14 近30天球队映射修复（第二轮）：Pafos/Pau/Annecy/Hajduk 串台 + 污染别名清理

SM 实测前置（_tmp_fx_verify_out3.json / _tmp_hajduk.json）：
- fx_19720992 (15464) = "Pafos FC vs Hajduk Split"（欧联）→ away 302(St.Pauli) 应为 Hajduk Split(sm=906)
- fx_19717650 (15542) = "Pau vs Annecy"（法乙）→ home 568(Pafos) 应为 Pau(390,sm=1838)；away 1655(阿纳西 sm=542 PAS Giannina) 应为 Annecy(382,sm=1447)
- fx_19766394 (15599) = "Pafos FC vs Salzburg"（欧联）→ 568=Pafos ✓ 539=Salzburg ✓ 无需改
- search: Pau=1838、Annecy=1447、Hajduk Split=906
- 本地已存在：390=Pau(1838)、382=Annecy(1447)
- 污染别名需清理：
  302 St.Pauli 上 '斯普利特海杜克'(855) primary —— Hajduk 应独立记录
  50 Halmstad 上 '沙尔克04'(99)+'Schalke 04'(100) —— 沙尔克是另一支德甲队，本地无记录，删别名防未来错配
  785 Nacional 上 '奥斯陆KFUM'(860) primary —— 奥斯陆KFUM=181 已有独立记录
  568 Pafos 上 '波城FC'(782) primary —— 波城=Pau(390)，568 真实身份是 Pafos FC
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    conn = await asyncpg.connect(DSN)
    async with conn.transaction():
        # ── 预检 ──
        assert await conn.fetchval("SELECT count(*) FROM teams WHERE sportmonks_id=906") == 0, "sm 906 已被占用"
        for tid in (390, 382):
            assert await conn.fetchval("SELECT count(*) FROM teams WHERE id=$1", tid) == 1, f"team {tid} 不存在"
        for mid in (15464, 15542, 15599, 15616):
            assert await conn.fetchval("SELECT count(*) FROM matches WHERE id=$1", mid) == 1, f"match {mid} 不存在"
        # 1655 引用必须仅限将迁移的两场
        cnt = await conn.fetchval("SELECT count(*) FROM matches WHERE home_team_id=1655 OR away_team_id=1655")
        assert cnt == 2, f"1655 引用数异常 cnt={cnt}"
        print("预检通过", flush=True)

        # ── 1) 新建 Hajduk Split ──
        hid = await conn.fetchval(
            "INSERT INTO teams (name_zh, name_en, sportmonks_id, created_at) "
            "VALUES ('斯普利特海杜克','Hajduk Split',906, now()) RETURNING id")
        await conn.execute(
            "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) "
            "VALUES ($1,'斯普利特海杜克','manual',true)", hid)
        print(f"新建 Hajduk Split id={hid}", flush=True)

        # ── 2) 比赛球队修正 ──
        # 15464: away 302 → hid
        await conn.execute("UPDATE matches SET away_team_id=$1 WHERE id=15464", hid)
        # 15542: home 568 → 390(Pau), away 1655 → 382(Annecy)
        await conn.execute("UPDATE matches SET home_team_id=390, away_team_id=382 WHERE id=15542")
        # 15616: home 1655 → 382(Annecy)
        await conn.execute("UPDATE matches SET home_team_id=382 WHERE id=15616")
        print("比赛修正 15464/15542/15616", flush=True)

        # ── 3) 删除污染别名 ──
        for aid, tid in ((855, 302), (99, 50), (100, 50), (860, 785), (782, 568)):
            n = await conn.execute("DELETE FROM team_aliases WHERE id=$1 AND team_id=$2", aid, tid)
            print(f"删别名 id={aid} team={tid} rows={n}", flush=True)

        # ── 4) 球队改名/补名 ──
        # 568 Pafos FC：name_zh 从 '波城FC' 改 '帕福斯'
        await conn.execute("UPDATE teams SET name_zh='帕福斯' WHERE id=568")
        # 390 Pau：补中文名
        await conn.execute("UPDATE teams SET name_zh='波城' WHERE id=390")
        n = await conn.fetchval("SELECT count(*) FROM team_aliases WHERE team_id=390 AND alias_name='波城FC'")
        if n == 0:
            await conn.execute(
                "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) "
                "VALUES (390,'波城FC','manual',true)")
        # 382 Annecy：补中文名
        await conn.execute("UPDATE teams SET name_zh='阿纳西' WHERE id=382")
        n = await conn.fetchval("SELECT count(*) FROM team_aliases WHERE team_id=382 AND alias_name='阿纳西'")
        if n == 0:
            await conn.execute(
                "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) "
                "VALUES (382,'阿纳西','manual',true)")
        # 785 Nacional：补中文名
        await conn.execute("UPDATE teams SET name_zh='葡萄牙国民' WHERE id=785")
        print("球队改名/补名 568/390/382/785", flush=True)

        # ── 5) 删除 1655（引用已迁移） ──
        await conn.execute("DELETE FROM team_aliases WHERE team_id=1655")
        await conn.execute("DELETE FROM team_season_stats WHERE team_id=1655")
        await conn.execute("DELETE FROM head_to_head WHERE home_team_id=1655 OR away_team_id=1655")
        n = await conn.execute("DELETE FROM teams WHERE id=1655")
        print(f"删除占位球队 1655 rows={n}", flush=True)

    # ── 复验 ──
    rows = await conn.fetch(
        """SELECT m.id, m.kickoff_time, l.name_zh AS lg, th.name_zh AS h, th.sportmonks_id AS h_sm,
                  ta.name_zh AS a, ta.sportmonks_id AS a_sm, m.sportmonks_fixture_id AS fx
           FROM matches m JOIN teams th ON th.id=m.home_team_id
           JOIN teams ta ON ta.id=m.away_team_id JOIN leagues l ON l.id=m.league_id
           WHERE m.id IN (15464,15542,15599,15616) ORDER BY m.id""")
    for r in rows:
        print("VERIFY", dict(r), flush=True)
    dup = await conn.fetch(
        "SELECT sportmonks_id, count(*) FROM teams WHERE sportmonks_id IS NOT NULL "
        "GROUP BY sportmonks_id HAVING count(*) > 1")
    print("SM_DUPS:", [dict(r) for r in dup], flush=True)
    for tid in (1655,):
        n = await conn.fetchval("SELECT count(*) FROM teams WHERE id=$1", tid)
        assert n == 0, f"占位 {tid} 未删除"
    # 别名污染复查
    bad = await conn.fetch(
        """SELECT ta.team_id, t.name_zh, ta.alias_name FROM team_aliases ta JOIN teams t ON t.id=ta.team_id
           WHERE ta.is_primary=true AND (ta.alias_name IN ('沙尔克04','斯普利特海杜克','奥斯陆KFUM','波城FC')
                 OR (ta.team_id=568 AND ta.alias_name='波城FC'))""")
    print("污染别名残留:", [dict(r) for r in bad], flush=True)
    print("ALL DONE", flush=True)
    await conn.close()


asyncio.run(main())
