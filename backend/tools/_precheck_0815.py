"""预检：19 支球队 SM id 占用情况 + 11 场比赛联赛归属"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

# (本地球队id, 中文名, SM id, 英文名, short)
TEAMS = [
    (1723, "基尔", 3611, "Holstein Kiel", "HKI"),
    (1724, "不伦瑞克", 3565, "Eintracht Braunschweig", "EBR"),
    (1725, "新未来SC", 17730, "NEOM SC", "NEO"),
    (1726, "迈季迈阿宽广", 17724, "Al-Fayha", "FAY"),
    (1727, "达曼协定", 10010, "Al Ettifaq", "ETT"),
    (1728, "利雅得", 17705, "Al Riyadh", "RIY"),
    (1729, "利雅得新月", 7011, "Al Hilal", "ALH"),
    (1730, "哈马赫费萨利", 6883, "Al Faisaly", "ALF"),
    (1731, "瓦尔韦克", 814, "RKC Waalwijk", "RKC"),
    (1732, "多德勒支", 822, "FC Dordrecht", "DOR"),
    (1733, "赫拉克勒斯", 1403, "Heracles Almelo", "HEA"),
    (1734, "登博思", 2385, "FC Den Bosch", "FDB"),
    (1735, "罗德兹", 9291, "Rodez", None),
    (1736, "兰斯", 1028, "Reims", "SdR"),
    (1737, "敦刻尔克", 1281, "Dunkerque", None),
    (1738, "圣埃蒂安", 108, "Saint-Étienne", "STE"),
    (1739, "克莱蒙", 6898, "Clermont", "CLE"),
    (1740, "伍尔弗汉普顿", 29, "Wolverhampton Wanderers", "WOL"),
    (1741, "布莱克本", 2, "Blackburn Rovers", "BBR"),
]

MATCHES = [15606, 15607, 15608, 15611, 15612, 15614, 15615, 15616, 15617, 15618, 15619]


async def main():
    conn = await asyncpg.connect(DSN)

    print("=== SM id 占用情况 ===")
    for tid, zh, smid, en, short in TEAMS:
        t = await conn.fetchrow(
            "SELECT id, name_zh, name_en, league_id, needs_review FROM teams WHERE sportmonks_id=$1", smid)
        if t:
            print(f"  SM {smid} ({zh}) -> 已占用: id={t['id']} zh={t['name_zh']} en={t['name_en']} lg={t['league_id']}")
        else:
            print(f"  SM {smid} ({zh}) -> 未占用（需新建映射到本队 {tid}）")

    print("\n=== 11 场比赛联赛归属 ===")
    for mid in MATCHES:
        m = await conn.fetchrow(
            "SELECT m.id, m.league_id, l.name_zh, l.sportmonks_id, "
            "m.home_team_id, m.away_team_id, m.sportmonks_fixture_id "
            "FROM matches m JOIN leagues l ON l.id=m.league_id WHERE m.id=$1", mid)
        print(f"  #{m['id']} lg={m['league_id']}({m['name_zh']}, sm={m['sportmonks_id']}) "
              f"home={m['home_team_id']} away={m['away_team_id']} fx={m['sportmonks_fixture_id']}")

    print("\n=== 已占用记录详情（需合并的） ===")
    for smid in (17730, 17724, 10010, 17705, 7011, 6883):
        t = await conn.fetchrow("SELECT * FROM teams WHERE sportmonks_id=$1", smid)
        if t:
            d = dict(t)
            print(f"  SM {smid}: id={d['id']} zh={d['name_zh']} en={d['name_en']} short={d['short_en']} lg={d['league_id']} review={d['needs_review']}")

    await conn.close()


asyncio.run(main())
