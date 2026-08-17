"""修复「未知联赛/其他联赛」分组比赛的联赛归属（SM fixture API 实测验证前置）

背景（2026-08-14，历史报告「近30天各联赛命中率」出现 21 场「未知联赛」分组）：
- sync_daily_matches 用 _find_league_by_name 匹配联赛；以下联赛名无记录/别名 → league_id 落空：
  解放者杯 / 沙职 / 欧超杯 / 亚冠精英（leagues 表缺失）与 英联赛杯（现存「英联杯」id=23，缺别名）
- 巴西杯/欧罗巴 5 场也落空（历史创建时联赛记录不存在，后期不回刷）
- 连带：3 场未来沙职比赛（15608/15611/15612）+ 1 场已取消亚冠精英（15581）同样 league_id NULL

SM fixture API 实测（2026-08-14）：
- 解放者杯 5 场 fixture 全部 Copa Libertadores（sm league=1122，参与者核对一致）
- 英联赛杯 3 场 fixture 全部 Carabao Cup（sm league=27，=英联杯 id=23 同一赛事）
- 巴西杯 5 场 fixture 全部 Copa do Brasil（sm league=654，=现存 id=16）
- 欧罗巴 5 场 fixture 全部 Europa League（sm league=5，=现存 id=14）
- 欧超杯 15591 fixture=19709410 → UEFA Super Cup（PSG vs Aston Villa）
- 沙职 2 场（15595/15594）：SM 搜索接口不可用 + 15594 无 SM fixture；按竞彩 venue「沙职」确定性归属
- 亚冠精英 15581（江原FC vs 大阪钢巴）：08-11 已标记 cancelled，SM 未收录，按 venue 归属

修复动作：
1. 新建 4 个联赛：解放者杯 / 沙职 / 欧超杯 / 亚冠精英（sportmonks_id 留空，避免改变 standings 同步行为）
2. 补别名「英联赛杯」→ 英联杯(id=23)，防未来 sync 再次落空
3. 25 场 matches.league_id 按 venue 更新（解放者杯5 / 沙职5 / 欧超杯1 / 英联杯3 / 巴西杯5 / 欧罗巴5 / 亚冠精英1）
4. 同步更新相关 predictions.league_id（图表分组走 match.league，此处保证两表一致）
"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

# venue -> (name_zh, name_en, country, 目标 league_id or None)
EXISTING = {
    "英联赛杯": 23,  # 英联杯 EFL Cup
    "巴西杯": 16,
    "欧罗巴": 14,
}
NEW_LEAGUES = [
    ("解放者杯", "Copa Libertadores", "South America"),
    ("沙职", "Saudi Pro League", "Saudi Arabia"),
    ("欧超杯", "UEFA Super Cup", "Europe"),
    ("亚冠精英", "AFC Champions League Elite", "Asia"),
]
VENUES = list(EXISTING) + [nl[0] for nl in NEW_LEAGUES]


async def main():
    conn = await asyncpg.connect(DSN)

    # ── 1. 预检 ──
    for name_zh in [nl[0] for nl in NEW_LEAGUES]:
        r = await conn.fetchrow("SELECT id, name_zh FROM leagues WHERE name_zh=$1", name_zh)
        if r:
            print(f"预检失败：联赛 {name_zh} 已存在 (id={r['id']})，中止")
            await conn.close()
            return
    for league_id in EXISTING.values():
        r = await conn.fetchrow("SELECT id, name_zh FROM leagues WHERE id=$1", league_id)
        if not r:
            print(f"预检失败：现存联赛 id={league_id} 不存在，中止")
            await conn.close()
            return

    # 预检受影响比赛（全部应 league_id IS NULL）
    rows = await conn.fetch(
        "SELECT id, venue, home_team_name, away_team_name, status FROM matches "
        "WHERE league_id IS NULL AND venue = ANY($1::text[]) ORDER BY kickoff_time",
        VENUES,
    )
    print(f"预检：league_id IS NULL 且 venue 匹配的比赛 {len(rows)} 场：")
    for r in rows:
        print(f"  {r['id']} [{r['venue']}] {r['home_team_name']} vs {r['away_team_name']} ({r['status']})")

    # ── 2. 新建联赛 ──
    new_ids = {}
    for name_zh, name_en, country in NEW_LEAGUES:
        new_id = await conn.fetchval(
            "INSERT INTO leagues (name_zh, name_en, country, season, active) "
            "VALUES ($1, $2, $3, NULL, true) RETURNING id",
            name_zh, name_en, country,
        )
        new_ids[name_zh] = new_id
        print(f"新建联赛: {name_zh} ({name_en}) id={new_id}")

    # ── 3. 补别名 ──
    has_alias = await conn.fetchval(
        "SELECT id FROM league_aliases WHERE league_id=23 AND alias_name='英联赛杯'")
    if not has_alias:
        await conn.execute(
            "INSERT INTO league_aliases (league_id, alias_name, source, is_primary) "
            "VALUES (23, '英联赛杯', 'sporttery.cn', false)")
        print("新增别名: 英联赛杯 → 英联杯(23)")
    else:
        print("别名已存在: 英联赛杯 → 英联杯(23)")

    # ── 4. 更新 matches.league_id ──
    league_by_venue = {**EXISTING, **new_ids}
    updated_matches = []
    for venue, lg_id in league_by_venue.items():
        n = await conn.execute(
            "UPDATE matches SET league_id=$1 WHERE league_id IS NULL AND venue=$2",
            lg_id, venue,
        )
        count = int(n.split()[-1]) if isinstance(n, str) else n
        updated_matches.append((venue, lg_id, count))
        print(f"matches.league_id 更新: {venue} → {lg_id} ({count} 场)")

    # 受影响的 match id（本次实际更新的）
    match_ids = await conn.fetch(
        "SELECT id FROM matches WHERE league_id IS NOT NULL AND venue = ANY($1::text[]) "
        "AND kickoff_time >= '2026-07-01'",
        VENUES,
    )
    ids = [r["id"] for r in match_ids]

    # ── 5. 更新 predictions.league_id ──
    n = await conn.execute(
        "UPDATE predictions SET league_id = m.league_id FROM matches m "
        "WHERE predictions.match_id = m.id AND predictions.match_id = ANY($1::bigint[]) "
        "AND predictions.league_id IS DISTINCT FROM m.league_id",
        ids,
    )
    count = int(n.split()[-1]) if isinstance(n, str) else n
    print(f"predictions.league_id 同步: {count} 条")

    # ── 6. 汇总 ──
    print("\n=== 汇总 ===")
    for venue, lg_id, cnt in updated_matches:
        print(f"  {venue} → league_id={lg_id} ({cnt} 场)")
    print(f"  共更新比赛 {len(ids)} 场，同步预测 {count} 条")

    await conn.close()
    print("\n完成。")


asyncio.run(main())
