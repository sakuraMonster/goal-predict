"""批量验证维度B：对近期有快照的比赛提取特征，检查 goal_line_shift / odds_drift 输出"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, OddsSnapshot
from app.predictor.features_b import FeatureEngineerB
from datetime import datetime, timedelta


async def main():
    async with async_session() as db:
        cutoff = datetime.now() - timedelta(hours=72)
        r = await db.execute(
            select(Match).options(joinedload(Match.league)).where(
                Match.id.in_(
                    select(OddsSnapshot.match_id).where(OddsSnapshot.snapshot_time >= cutoff).group_by(OddsSnapshot.match_id)
                )
            )
        )
        matches = r.unique().scalars().all()
        print(f"共 {len(matches)} 场比赛\n")

        rows = []
        for m in matches:
            try:
                eng = FeatureEngineerB(db)
                df = await eng.extract_features(m.id)
                if df is None or df.empty:
                    continue
                f = df.iloc[0].to_dict()
                ln = m.league.name_zh if m.league else "无联赛"
                rows.append((
                    m.id, ln, m.home_team_name, m.away_team_name,
                    f.get("goal_line_market", 0),
                    f.get("goal_line_shift", 0),
                    f.get("odds_drift_over_mean", 0),
                    f.get("odds_drift_consensus", 0),
                    f.get("goal_line_drop_from_peak", 0),
                    f.get("goal_line_market_old", 0),
                    f.get("goal_line_drop_from_peak_old", 0),
                ))
            except Exception as e:
                print(f"  [ERR] {m.id} {m.home_team_name}vs{m.away_team_name}: {e}")

        # 按 shift 绝对值排序
        rows.sort(key=lambda x: abs(x[5]) if x[5] else 0, reverse=True)
        print(f"{'ID':>7} {'联赛':<8} {'主队':<10} {'客队':<10} {'GL':>5} {'shift':>7} {'drift':>7} {'cons':>6} {'drop':>6} {'GL_old':>7} {'drop_old':>8}")
        for row in rows:
            print(f"{row[0]:>7} {row[1]:<8} {row[2]:<10} {row[3]:<10} {row[4]:>5} {row[5]:>7} {row[6]:>7} {row[7]:>6} {row[8]:>6} {row[9]:>7} {row[10]:>8}")


asyncio.run(main())
