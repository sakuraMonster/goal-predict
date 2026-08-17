"""检查日职/德乙比赛的主盘线(goal_line)计算是否正确"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, League, OddsSnapshot
from app.predictor.features_b import FeatureEngineerB
from collections import Counter


async def main():
    now = datetime.now()
    qs = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=12)
    qe = now + timedelta(days=3)

    async with async_session() as db:
        rl = await db.execute(select(League.id, League.name_zh))
        lmap = {r[0]: r[1] for r in rl}
        name2id = {v: k for k, v in lmap.items()}

        targets = []
        for lg in ("日职联", "日职", "德乙"):
            lid = name2id.get(lg)
            if lid:
                rq = await db.execute(
                    select(Match).where(
                        Match.league_id == lid,
                        Match.kickoff_time >= qs,
                        Match.kickoff_time <= qe,
                    ).order_by(Match.kickoff_time)
                )
                targets.extend(list(rq.scalars().all()))
        if not targets:
            print("未找到目标比赛")
            return

        feat = FeatureEngineerB(db)
        print(f"找到 {len(targets)} 场目标比赛\n")
        for m in targets:
            lg = lmap.get(m.league_id, "?")
            try:
                df = await feat.extract_features(m.id)
                if df.empty:
                    print(f"{m.id} [{lg}] {m.home_team_name} vs {m.away_team_name}: 特征为空")
                    continue
                f = df.iloc[0]
                print(f"{m.id} [{lg}] {m.home_team_name} vs {m.away_team_name} "
                      f"| 开赛{m.kickoff_time:%m-%d %H:%M} | GL={f['goal_line_market']:.2f} "
                      f"shift={f['goal_line_shift']:.2f} drift={f['odds_drift_over_mean']:.3f}")

                # 逐公司主盘线详情
                rs = await db.execute(
                    select(OddsSnapshot).where(OddsSnapshot.match_id == m.id)
                    .order_by(OddsSnapshot.snapshot_time.asc())
                )
                all_odds = list(rs.scalars().all())
                by_time = {}
                for o in all_odds:
                    by_time.setdefault(o.snapshot_time, []).append(o)
                latest_t = sorted(by_time.keys())[-1]
                latest = by_time[latest_t]
                bm_full = {}
                for o in latest:
                    if o.goal_line is None or o.over_odds is None or o.under_odds is None:
                        continue
                    gl = round(float(o.goal_line), 2)
                    diff = abs(o.over_odds - o.under_odds)
                    if o.bookmaker not in bm_full or diff < bm_full[o.bookmaker][1]:
                        bm_full[o.bookmaker] = (gl, diff)
                for bm, (gl, diff) in sorted(bm_full.items()):
                    print(f"    [{bm}] 主盘线={gl} diff={diff:.3f}")
                if bm_full:
                    c = Counter(v[0] for v in bm_full.values())
                    print(f"    → 最新时刻主盘线众数: {c.most_common(3)} (快照{latest_t:%m-%d %H:%M})")
            except Exception as e:
                print(f"{m.id} [{lg}]: ERR {e}")


asyncio.run(main())
