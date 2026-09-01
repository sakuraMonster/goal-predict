"""修复：把「该 match 有 hafu 的快照」的 hafu 复制到「MFP 指向但 hafu=NULL」的快照。

背景：回填 hafu 时，部分场次因 _snap_equal 未匹配到 MFP 指向的快照而 insert 了新快照，
导致 MFP.odds_snapshot_id 指向的快照 hafu_odds_json 仍为 NULL，接口读不到半全场赔率。
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.dirname(HERE)
sys.path.insert(0, BACKEND_ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_ROOT, ".env"))

from sqlalchemy import text

from app.db.database import async_session


async def main():
    async with async_session() as db:
        # 找到 MFP 指向的、hafu=NULL、但该 match 存在 hafu 非空快照的 snapshot
        res = await db.execute(text("""
            SELECT s.id, s.match_id
            FROM jczq_play_odds_snapshots s
            WHERE s.id IN (SELECT odds_snapshot_id FROM market_flow_predictions WHERE odds_snapshot_id IS NOT NULL)
              AND s.hafu_odds_json IS NULL
              AND EXISTS (
                  SELECT 1 FROM jczq_play_odds_snapshots s2
                  WHERE s2.match_id = s.match_id AND s2.hafu_odds_json IS NOT NULL
              )
        """))
        rows = res.fetchall()
        print(f"待修复 {len(rows)} 条快照")

        fixed = 0
        for sid, mid in rows:
            await db.execute(text("""
                UPDATE jczq_play_odds_snapshots s
                SET hafu_odds_json = (
                    SELECT hafu_odds_json FROM jczq_play_odds_snapshots s2
                    WHERE s2.match_id = :mid AND s2.hafu_odds_json IS NOT NULL
                    ORDER BY s2.snapshot_time DESC LIMIT 1
                )
                WHERE s.id = :sid
            """), {"sid": sid, "mid": mid})
            fixed += 1
        await db.commit()
        print(f"修复完成 {fixed} 条")


if __name__ == "__main__":
    asyncio.run(main())
