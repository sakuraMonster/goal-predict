"""补充 08-15 周期球队中文展示名"""
import asyncio
import asyncpg

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

UPDATES = {
    1697: "秋田蓝色闪电",
    1698: "富山胜利",
    301: "海登海姆",
    1202: "阿尔维卡",
    422: "圣克拉拉",
}


async def main():
    conn = await asyncpg.connect(DSN)
    for tid, zh in UPDATES.items():
        await conn.execute("UPDATE teams SET name_zh=$1 WHERE id=$2", zh, tid)
        print(f"team {tid} name_zh -> {zh}")
    await conn.close()


asyncio.run(main())
