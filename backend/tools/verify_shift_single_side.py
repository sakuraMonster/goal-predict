"""验证维度B单边数据兼容修复：检查各场次 goal_line_shift / odds_drift 是否非零"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from app.db.database import async_session
from app.predictor.features_b import FeatureEngineerB
from sqlalchemy import select
from app.db.models import Match, League


async def main():
    match_ids = [15532, 15550, 15547, 15567, 15565, 15522, 15533, 15529, 15520]
    async with async_session() as db:
        feat = FeatureEngineerB(db)
        # 联赛名
        lq = await db.execute(select(League.id, League.name_zh))
        lmap = {r[0]: r[1] for r in lq}
        for mid in match_ids:
            mq = await db.execute(select(Match).where(Match.id == mid))
            m = mq.scalar_one_or_none()
            if not m:
                print(f"{mid}: 比赛不存在")
                continue
            lg = lmap.get(m.league_id, "?")
            try:
                df = await feat.extract_features(mid)
                if df.empty:
                    print(f"{mid} [{lg}] {m.home_team_name} vs {m.away_team_name}: EMPTY")
                    continue
                f = df.iloc[0]
                print(f"{mid} [{lg}] {m.home_team_name} vs {m.away_team_name}: "
                      f"GL={f['goal_line_market']:.2f} shift={f['goal_line_shift']:.2f} "
                      f"drift={f['odds_drift_over_mean']:.3f} cons={f['odds_drift_consensus']:.2f} "
                      f"old_drop={f['goal_line_drop_from_peak_old']:.2f}")
            except Exception as e:
                print(f"{mid} [{lg}]: ERR {e}")


asyncio.run(main())
