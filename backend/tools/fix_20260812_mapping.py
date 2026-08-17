"""修复 1719（罗萨里奥中央错误映射）→ 合并到 1591 + 15597 补 fixture（含 SM API 实测验证）

现状：
- 1591 = Rosario Central（sm=3365 唯一持有者，正确记录，已有 team_season_stats）
- 1719 = 重复占位：name_en 被错误写成 'Cercle Brugge'，sm=2641（Cercle Brugge 真实 ID，从未入库）
- 15597 (2040828 罗萨里奥中央 vs 科林蒂安) home_team_id=1719、无 fixture

修复：
1. SM API 验证 team3365=Rosario Central、fixture19712160 含 3365/303、08-14 开赛 → 通过才执行
2. 15597: home_team_id 1719→1591，sportmonks_fixture_id=19712160
3. 别名迁移：'罗萨里奥中央'(主)/'RCL' → 1591；删除 1719 的 3 条别名（含错误的 Cercle Brugge）
4. 1591 补 name_zh='罗萨里奥中央'
5. 删除 team 1719（先删别名再删球队，避免 FK 冲突）
"""
import asyncio
import asyncpg
from dotenv import load_dotenv
from app.collector.sportmonks.client import SportMonksClient

DSN = "postgresql://postgres:postgres@localhost/football_prediction"


async def main():
    load_dotenv()
    conn = await asyncpg.connect(DSN)

    # ── 1. 预检 ──
    t1591 = await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE id=1591")
    t1719 = await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE id=1719")
    m15597 = await conn.fetchrow(
        "SELECT id, jc_match_id, home_team_id, away_team_id, sportmonks_fixture_id FROM matches WHERE id=15597")
    refs = await conn.fetch(
        "SELECT id, home_team_id, away_team_id FROM matches WHERE home_team_id=1719 OR away_team_id=1719")
    print("预检 1591:", dict(t1591))
    print("预检 1719:", dict(t1719))
    print("预检 15597:", dict(m15597))
    print("预检 引用1719的matches:", [dict(r) for r in refs])

    if not t1591 or not t1719 or not m15597:
        print("预检失败：1591/1719/15597 不存在，中止")
        await conn.close()
        return
    if t1591["sportmonks_id"] != 3365 or t1719["sportmonks_id"] != 2641:
        print("预检失败：1591/1719 的 sportmonks_id 与预期不符，中止")
        await conn.close()
        return
    if any(r["away_team_id"] == 1719 for r in refs):
        print("预检失败：1719 还被作为客队引用，合并方案需先确认，中止")
        await conn.close()
        return

    # ── 2. SM API 实测验证 ──
    sm = SportMonksClient()
    team = await sm.get_team_by_id(3365)
    fx = await sm.get_fixture_by_id(19712160, includes="participants")
    await sm.close()

    tname = (team or {}).get("name", "")
    parts = fx.get("participants", [])
    pids = [p.get("id") for p in parts if isinstance(p, dict)]
    pmeta = [(p.get("id"), p.get("name"), (p.get("meta") or {}).get("location")) for p in parts]
    start = fx.get("starting_at", "")
    print(f"SM 验证 team3365 = {tname}")
    print(f"SM 验证 fixture19712160 start={start} participants={pmeta}")

    ok = (
        tname == "Rosario Central"
        and 3365 in pids
        and 303 in pids
        and start.startswith("2026-08-14")
    )
    print("SM 验证:", "PASS" if ok else "FAIL")
    if not ok:
        print("验证未通过，不执行任何写入，中止")
        await conn.close()
        return

    # ── 3. 执行合并修复 ──
    async with conn.transaction():
        # 3.1 15597 指向正确球队 + 补 fixture
        await conn.execute(
            "UPDATE matches SET home_team_id=1591, sportmonks_fixture_id=19712160 WHERE id=15597"
        )
        # 3.2 别名迁移到 1591（先查重再插入）
        await conn.execute(
            "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) "
            "SELECT 1591, CAST($1 AS VARCHAR), 'sporttery.cn', true "
            "WHERE NOT EXISTS (SELECT 1 FROM team_aliases WHERE team_id=1591 AND alias_name=CAST($1 AS VARCHAR))",
            "罗萨里奥中央",
        )
        await conn.execute(
            "INSERT INTO team_aliases (team_id, alias_name, source, is_primary) "
            "SELECT 1591, CAST($1 AS VARCHAR), 'sporttery.cn', false "
            "WHERE NOT EXISTS (SELECT 1 FROM team_aliases WHERE team_id=1591 AND alias_name=CAST($1 AS VARCHAR))",
            "RCL",
        )
        # 3.3 1591 补中文名（原为英文名占位）
        if not t1591["name_zh"] or all(ord(c) < 128 for c in t1591["name_zh"]):
            await conn.execute("UPDATE teams SET name_zh='罗萨里奥中央' WHERE id=1591")
        # 3.4 删除 1719：先删别名再删球队
        await conn.execute("DELETE FROM team_aliases WHERE team_id=1719")
        await conn.execute("DELETE FROM teams WHERE id=1719")
    print("合并修复完成：15597→1591+fixture=19712160；别名迁移；1719 已删除")

    # ── 4. 回读确认 ──
    t = await conn.fetchrow("SELECT id, name_zh, name_en, sportmonks_id FROM teams WHERE id=1591")
    a = await conn.fetch("SELECT alias_name, source, is_primary FROM team_aliases WHERE team_id=1591")
    m = await conn.fetchrow("SELECT id, jc_match_id, home_team_id, sportmonks_fixture_id FROM matches WHERE id=15597")
    gone = await conn.fetchrow("SELECT COUNT(*) AS n FROM teams WHERE id=1719")
    dup = await conn.fetchrow("SELECT sportmonks_id, COUNT(*) AS n FROM teams WHERE sportmonks_id IN (3365, 2641) GROUP BY sportmonks_id")
    print("回读 1591:", dict(t))
    print("回读 1591 aliases:", [dict(x) for x in a])
    print("回读 15597:", dict(m))
    print("回读 1719 残留:", dict(gone))
    print("回读 sm 唯一性:", dict(dup) if dup else "3365/2641 均无持有")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
