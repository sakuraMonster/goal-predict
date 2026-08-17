"""调试指定比赛的维度B初盘共识线：新旧逻辑对比（双赔率 vs 单边兼容）"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import OddsSnapshot, Match
from collections import Counter

MATCH_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 15550


async def main():
    async with async_session() as db:
        mq = await db.execute(select(Match).where(Match.id == MATCH_ID))
        m = mq.scalar_one_or_none()
        print(f"比赛 {MATCH_ID}: {m.home_team_name} vs {m.away_team_name}")

        result = await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id == MATCH_ID)
            .order_by(OddsSnapshot.snapshot_time.asc())
        )
        all_odds = list(result.scalars().all())
        print(f"总快照数: {len(all_odds)}")

        by_time: dict = {}
        for o in all_odds:
            by_time.setdefault(o.snapshot_time, []).append(o)

        for t in sorted(by_time.keys()):
            recs = by_time[t]
            print(f"\n=== {t} (共{len(recs)}条) ===")
            # 旧逻辑：只算双赔率公司的 diff 最小主盘线
            bm_full = {}   # bm -> (gl, diff)
            bm_partial = {}  # bm -> [gl]
            for o in recs:
                gl = round(float(o.goal_line), 2) if o.goal_line is not None else None
                if gl is None or (o.over_odds is None and o.under_odds is None):
                    continue
                if o.over_odds is not None and o.under_odds is not None:
                    diff = abs(o.over_odds - o.under_odds)
                    if o.bookmaker not in bm_full or diff < bm_full[o.bookmaker][1]:
                        bm_full[o.bookmaker] = (gl, diff)
                else:
                    bm_partial.setdefault(o.bookmaker, []).append(gl)

            for bm, (gl, diff) in sorted(bm_full.items()):
                print(f"  [双] {bm}: 主盘线={gl} diff={diff:.3f}")
            for bm, gls in sorted(bm_partial.items()):
                mid = gls[0] if len(set(gls)) == 1 else sorted(gls)[len(gls) // 2]
                print(f"  [单] {bm}: 单边线={gls} → 取{mid}")

            full_gls = [v[0] for v in bm_full.values()]
            if full_gls:
                c = Counter(full_gls)
                print(f"  → 旧共识: {c.most_common(1)[0][0]} (分布{c.most_common(4)})")

            all_gls = [v for v in bm_full.values()]
            all_gls += [(gl, None) for gls in bm_partial.values() for gl in gls]
            if all_gls:
                new_votes = [v[0] for v in bm_full.values()]
                for bm, gls in bm_partial.items():
                    if bm not in bm_full:
                        new_votes.append(gls[0] if len(set(gls)) == 1 else sorted(gls)[len(gls) // 2])
                c2 = Counter(new_votes)
                print(f"  → 新共识: {c2.most_common(1)[0][0]} (分布{c2.most_common(4)})")

            # 打印所有原始行
            for o in recs:
                gl = round(float(o.goal_line), 2) if o.goal_line is not None else None
                print(f"    [{o.bookmaker}] GL={gl} over={o.over_odds} under={o.under_odds}")


asyncio.run(main())
