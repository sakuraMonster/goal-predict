"""对比 SNAP 降级/升级规则的命中率：当前实现 vs 修正实现
当前: 降级 effective=int(λ)-1 → snap_top2 中心下移（3.05→[2,1]，丢3）
修正: 降级 effective=int(λ)-0.5 → snap 覆盖 [int-1, int]（3.05→[2,3]，保留3）
升级同理: effective=int(λ)+1.5 → [int+1, int+2]
"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta, date
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction, League
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB

MAX_GOALS = 6

def snap_effective_old(lam: float) -> float:
    frac = lam - int(lam)
    if frac < 0.05:
        return float(max(0, int(lam) - 1))
    elif frac > 0.93:
        return float(min(MAX_GOALS, int(lam) + 2))
    return lam

def snap_effective_new(lam: float) -> float:
    frac = lam - int(lam)
    if frac < 0.05:
        return float(max(0, int(lam) - 0.5))  # 中心落在 int-0.5 → [int-1, int]
    elif frac > 0.93:
        return float(min(MAX_GOALS, int(lam) + 1.5))  # 中心落在 int+1.5 → [int+1, int+2]
    return lam

def top2(eff: float) -> list:
    dists = [(i, abs(eff - i)) for i in range(MAX_GOALS + 1)]
    dists.sort(key=lambda x: x[1])
    return [dists[0][0], dists[1][0]]


async def main():
    today = date.today()
    qs = datetime(today.year, today.month, today.day, 12, 0, 0) - timedelta(days=30)
    qe = datetime(today.year, today.month, today.day, 12, 0, 0) + timedelta(days=1)

    async with async_session() as db:
        feat = FeatureEngineerB(db)
        model_c = ModelC()
        rl = await db.execute(select(League.id, League.name_zh))
        lmap = {r[0]: r[1] for r in rl}

        rq = await db.execute(
            select(Match).options(joinedload(Match.league))
            .where(Match.kickoff_time >= qs, Match.kickoff_time < qe)
            .order_by(Match.kickoff_time)
        )
        matches = list(rq.unique().scalars().all())

        stats = defaultdict(lambda: {"n": 0, "old_hit": 0, "new_hit": 0, "diff": 0})
        total_n = total_old = total_new = 0
        diff_cases = []

        for m in matches:
            lg = lmap.get(m.league_id, "?")
            pr = await db.execute(select(Prediction).where(Prediction.match_id == m.id))
            pred = pr.scalar_one_or_none()
            actual = pred.actual_total_goals if pred else None
            if actual is None:
                continue
            try:
                df = await feat.extract_features(m.id)
                if df.empty:
                    continue
                features = df.iloc[0].to_dict()
            except Exception:
                continue
            rc = model_c.predict(features, lg)
            lam = rc["expected_goals"]

            old_snap = top2(snap_effective_old(lam))
            new_snap = top2(snap_effective_new(lam))
            old_hit = actual in old_snap
            new_hit = actual in new_snap

            total_n += 1
            total_old += old_hit
            total_new += new_hit
            stats[lg]["n"] += 1
            stats[lg]["old_hit"] += old_hit
            stats[lg]["new_hit"] += new_hit
            if old_snap != new_snap:
                stats[lg]["diff"] += 1
                d = (old_hit, new_hit)
                if d in ((False, True), (True, False)):
                    diff_cases.append((m, lg, lam, old_snap, new_snap, actual, d))

        print(f"已结算 {total_n} 场")
        print(f"旧规则命中: {total_old}/{total_n} = {total_old/total_n*100:.1f}%")
        print(f"新规则命中: {total_new}/{total_n} = {total_new/total_n*100:.1f}%")
        print(f"净变化: {total_new - total_old:+d}\n")

        print("--- 按联赛 ---")
        for lg in sorted(stats, key=lambda x: -stats[x]["n"]):
            st = stats[lg]
            print(f"  {lg}: n={st['n']} 旧={st['old_hit']}({st['old_hit']/st['n']*100:.0f}%) "
                  f"新={st['new_hit']}({st['new_hit']/st['n']*100:.0f}%) 变动{st['diff']}场")

        improved = [c for c in diff_cases if c[6] == (False, True)]
        worsened = [c for c in diff_cases if c[6] == (True, False)]
        print(f"\n改善 {len(improved)} 场 / 恶化 {len(worsened)} 场")
        for m, lg, lam, os_, ns, actual, _ in improved[:10]:
            print(f"  改善: {m.id} {m.home_team_name} vs {m.away_team_name}({lg}) λ={lam:.2f} "
                  f"SNAP {os_}→{ns} 实际{actual}球")
        for m, lg, lam, os_, ns, actual, _ in worsened[:10]:
            print(f"  恶化: {m.id} {m.home_team_name} vs {m.away_team_name}({lg}) λ={lam:.2f} "
                  f"SNAP {os_}→{ns} 实际{actual}球")


asyncio.run(main())
