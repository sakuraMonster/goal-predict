"""2026-08-14 近30天球队映射修复：双 primary 别名污染 + 占位球队清理 + team_lg 修正

背景（SM 实测前置）：
- 多支球队共用同一 team 记录（双 primary 别名），导致近30天未结算比赛指向错误球队：
  424 卡萨皮亚+温哥华白帽、71 拉齐奥+阿尔克马尔、1637 大宫松鼠+第戎、215 海牙+科罗拉多急流
- 占位/错误记录：1638(新潟天鹅 sm=19706 Alresford)、1656(蒙彼利埃 sm=259895 Dutemple)、
  576(波鸿 sm=587 Boca Juniors)
- SM fixture name 实测：15485=Vancouver Whitecaps vs LAFC、15530=Omiya vs Albirex Niigata、
  15543=Montpellier vs Dijon、15545=AZ vs ADO Den Haag、15535=West Ham vs Portsmouth
- SM 球队 id：Vancouver Whitecaps=292、Dijon=6842、VfL Bochum=999、AZ=61(本地471)、
  ADO Den Haag=1128(本地510)、Albirex Niigata=3607(本地570)、Montpellier=581(本地411)
- SM league 27 = Carabao Cup → 本地 23 英联杯 sportmonks_id=27
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

NEW_TEAMS = [
    # (name_zh, name_en, sm_id, league_id, primary_alias)  —— 仅温哥华白帽需新建
    ("温哥华白帽", "Vancouver Whitecaps", 292, 12, "温哥华白帽"),
]

RENAME_ALIAS = [
    # (team_id, new_name_zh, primary_alias)
    (510, "海牙", "海牙"),
    (570, "新潟天鹅", "新潟天鹅"),
    (411, "蒙彼利埃", "蒙彼利埃"),
    (471, "阿尔克马尔", "阿尔克马尔"),
    (1682, "第戎", "第戎"),      # Dijon 现存记录补中文名
    (456, "波鸿", "波鸿"),      # VfL Bochum 现存记录补主别名
]

MATCH_FIX = [
    # (match_id, home_team_id, away_team_id, note)
    (15485, "NEW:温哥华白帽", "keep", "温哥华白帽 vs 洛杉矶FC"),
    (15530, "keep", 570, "大宫松鼠 vs 新潟天鹅"),
    (15543, 411, 1682, "蒙彼利埃 vs 第戎"),
    (15545, 471, 510, "AZ vs 海牙"),
    (15607, "keep", 456, "不伦瑞克 vs 波鸿"),
]

DEL_ALIAS = [
    (1637, 787),  # '第戎' primary（1637=大宫松鼠）
    (71, 816),    # '阿尔克马尔' primary
    (71, 692),    # 'AZZ'
    (424, 415),   # '温哥华白帽' primary
    (424, 416),   # 'Vancouver Whitecaps'
    (215, 791),   # '海牙' primary
]

RENAME_ZH = [(1637, "大宫松鼠")]

DEL_TEAMS = [576, 1638, 1656]  # 576 波鸿(Boca)/1638 新潟天鹅(Alresford)/1656 蒙彼利埃(Dutemple)

TEAM_LG = [
    (256, 13),   # 瓦斯科达伽马 意甲4 -> 巴甲13
    (295, 1),    # West Ham 英冠22 -> 英超1
    (443, 26),   # 利雅得青年 韩K6 -> 沙职26
    (457, 26),   # 胡巴尔卡德西亚 韩K6 -> 沙职26
]


async def main():
    conn = await asyncpg.connect(DSN)
    async with conn.transaction():
        # ── 预检断言 ──
        for _, _, sm, _, _ in NEW_TEAMS:
            n = await conn.fetchval("SELECT count(*) FROM teams WHERE sportmonks_id=$1", sm)
            assert n == 0, f"sm {sm} 已被占用"
        for mid, _, _, _ in MATCH_FIX:
            n = await conn.fetchval("SELECT count(*) FROM matches WHERE id=$1", mid)
            assert n == 1, f"match {mid} 不存在"
        for tid in RENAME_ALIAS:
            n = await conn.fetchval("SELECT count(*) FROM teams WHERE id=$1", tid[0])
            assert n == 1, f"team {tid[0]} 不存在"
        for tid in DEL_TEAMS:
            # 占位球队除待修复比赛外不得有其他引用
            cnt = await conn.fetchval(
                "SELECT count(*) FROM matches WHERE home_team_id=$1 OR away_team_id=$1", tid)
            nxt = await conn.fetchval(
                "SELECT count(*) FROM matches WHERE (home_team_id=$1 OR away_team_id=$1) "
                "AND id NOT IN (15485,15530,15543,15545,15607)", tid)
            assert cnt == 1 and nxt == 0, f"team {tid} 引用数异常 cnt={cnt} nxt={nxt}"
        print("预检通过", flush=True)

        # ── 1) 新建球队 ──
        new_id = {}
        for name_zh, name_en, sm, lg, alias in NEW_TEAMS:
            tid = await conn.fetchval(
                "INSERT INTO teams (name_zh, name_en, sportmonks_id, league_id, created_at) "
                "VALUES ($1,$2,$3,$4, now()) RETURNING id", name_zh, name_en, sm, lg)
            await conn.execute(
                "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) "
                "VALUES ($1,$2,'manual',true)", tid, alias)
            new_id[name_zh] = tid
            print(f"新建球队 {name_zh} id={tid}", flush=True)

        # ── 2) 正确球队补中文名 + 主别名 ──
        for tid, name_zh, alias in RENAME_ALIAS:
            await conn.execute("UPDATE teams SET name_zh=$1 WHERE id=$2", name_zh, tid)
            n = await conn.fetchval(
                "SELECT count(*) FROM team_aliases WHERE team_id=$1 AND alias_name=$2", tid, alias)
            if n == 0:
                await conn.execute(
                    "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) "
                    "VALUES ($1,$2,'manual',true)", tid, alias)
            print(f"更新球队 {tid} name_zh={name_zh}", flush=True)

        # ── 3) 比赛球队修正 ──
        def resolve(v):
            if isinstance(v, str) and v.startswith("NEW:"):
                return new_id[v[len("NEW:"):]]
            return v

        for mid, home, away, note in MATCH_FIX:
            if home != "keep":
                await conn.execute(
                    "UPDATE matches SET home_team_id=$1 WHERE id=$2", resolve(home), mid)
            if away != "keep":
                await conn.execute(
                    "UPDATE matches SET away_team_id=$1 WHERE id=$2", resolve(away), mid)
            print(f"比赛 {mid} 修正 {note}", flush=True)

        # ── 4) 删除污染别名 ──
        for tid, aid in DEL_ALIAS:
            n = await conn.execute("DELETE FROM team_aliases WHERE id=$1 AND team_id=$2", aid, tid)
            print(f"删别名 id={aid} team={tid} rows={n}", flush=True)

        # ── 5) 球队改名 ──
        for tid, name_zh in RENAME_ZH:
            await conn.execute("UPDATE teams SET name_zh=$1 WHERE id=$2", name_zh, tid)
            print(f"改名 team {tid} -> {name_zh}", flush=True)

        # ── 6) 删除占位球队（先清 FK 引用） ──
        for tid in DEL_TEAMS:
            await conn.execute("DELETE FROM team_aliases WHERE team_id=$1", tid)
            await conn.execute("DELETE FROM team_season_stats WHERE team_id=$1", tid)
            await conn.execute(
                "DELETE FROM head_to_head WHERE home_team_id=$1 OR away_team_id=$1", tid)
            n = await conn.execute("DELETE FROM teams WHERE id=$1", tid)
            print(f"删除占位球队 {tid} rows={n}", flush=True)

        # ── 7) team_lg 修正 ──
        for tid, lg in TEAM_LG:
            await conn.execute("UPDATE teams SET league_id=$1 WHERE id=$2", lg, tid)
            print(f"team_lg {tid} -> {lg}", flush=True)

        # ── 8) 联赛 23 英联杯 sportmonks_id=27（SM Carabao Cup） ──
        await conn.execute("UPDATE leagues SET sportmonks_id=27 WHERE id=23")
        print("英联杯 sportmonks_id=27", flush=True)

    # ── 复验 ──
    rows = await conn.fetch(
        """SELECT m.id, m.kickoff_time, l.name_zh AS lg, th.name_zh AS h, ta.name_zh AS a,
                  th.sportmonks_id AS h_sm, ta.sportmonks_id AS a_sm
           FROM matches m JOIN teams th ON th.id=m.home_team_id
           JOIN teams ta ON ta.id=m.away_team_id JOIN leagues l ON l.id=m.league_id
           WHERE m.id IN (15485,15530,15535,15543,15545,15607) ORDER BY m.id""")
    for r in rows:
        print("VERIFY", dict(r), flush=True)
    dup = await conn.fetch(
        "SELECT sportmonks_id, count(*) FROM teams WHERE sportmonks_id IS NOT NULL "
        "GROUP BY sportmonks_id HAVING count(*) > 1")
    print("SM_DUPS:", [dict(r) for r in dup], flush=True)
    for tid in DEL_TEAMS:
        n = await conn.fetchval("SELECT count(*) FROM teams WHERE id=$1", tid)
        assert n == 0, f"占位 {tid} 未删除"
    print("ALL DONE", flush=True)
    await conn.close()


asyncio.run(main())
