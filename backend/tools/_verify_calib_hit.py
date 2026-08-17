"""日职联/荷甲 calib 调整前后命中率对比（隔离 calib 变量：同特征，仅切换 calib）"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

from app.db.database import async_session
from app.db.models import Prediction, Match
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

BEIJING_TZ = timezone(timedelta(hours=8))
OLD_CALIB = {"日职联": 0.880, "荷甲": 0.95}  # 荷甲原走 default 0.95


def hit(total_goals, snap):
    return total_goals in list(snap) if snap else False


async def main():
    now = datetime.now(BEIJING_TZ).replace(tzinfo=None)
    since = now - timedelta(days=30)
    model_c = ModelC()

    async with async_session() as db:
        feat_engine = FeatureEngineerB(db)
        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= since, Prediction.kickoff_time < now))
            .order_by(Prediction.kickoff_time)
        )
        preds = list(result.unique().scalars().all())

        targets = [
            p for p in preds
            if p.actual_home_score is not None
            and p.match and p.match.league
            and p.match.league.name_zh in ("日职联", "荷甲")
        ]

        print(f"近30天 日职联+荷甲 已结算场次: {len(targets)}\n")

        stats = {}
        for p in targets:
            ln = p.match.league.name_zh
            tg = (p.actual_home_score or 0) + (p.actual_away_score or 0)
            s = stats.setdefault(ln, {
                "n": 0, "db_hit": 0, "old_hit": 0, "new_hit": 0,
                "old_l": 0.0, "new_l": 0.0, "changed": 0, "improved": 0,
            })
            s["n"] += 1

            # DB 存储的旧预测命中
            if hit(tg, p.snap_top2_c):
                s["db_hit"] += 1

            try:
                features_df = await feat_engine.extract_features(p.match_id)
                if features_df.empty:
                    print(f"  [skip] match_id={p.match_id} 特征为空")
                    continue
                features = features_df.iloc[0].to_dict()

                # 新 calib
                new_l = model_c.predict(features, ln)["expected_goals"]

                # 旧 calib（临时还原，隔离变量）
                saved = ModelC.LEAGUE_PARAMS[ln]["calib"]
                ModelC.LEAGUE_PARAMS[ln]["calib"] = OLD_CALIB[ln]
                old_l = model_c.predict(features, ln)["expected_goals"]
                ModelC.LEAGUE_PARAMS[ln]["calib"] = saved

                old_snap = snap_top2(old_l)
                new_snap = snap_top2(new_l)
                old_h = hit(tg, old_snap)
                new_h = hit(tg, new_snap)

                s["old_l"] += old_l
                s["new_l"] += new_l
                if old_h:
                    s["old_hit"] += 1
                if new_h:
                    s["new_hit"] += 1
                if old_snap != new_snap:
                    s["changed"] += 1
                    if new_h and not old_h:
                        s["improved"] += 1

                hn = p.match.home_team.name_zh if p.match.home_team else (p.match.home_team_name or "?")
                an = p.match.away_team.name_zh if p.match.away_team else (p.match.away_team_name or "?")
                flag = ""
                if old_snap != new_snap:
                    flag = f"  SNAP {old_snap}->{new_snap}  命中 {old_h}->{new_h}"
                print(f"  {ln} {p.kickoff_time:%m-%d} {hn} vs {an}  实际{tg}  λ {old_l:.2f}->{new_l:.2f}{flag}")

            except Exception as e:
                print(f"  [error] match_id={p.match_id} ({ln}): {type(e).__name__}: {e}")

        print("\n" + "=" * 90)
        for ln, s in stats.items():
            n = s["n"]
            print(f"\n{ln} (n={n}):")
            print(f"  DB 旧预测命中: {s['db_hit']}/{n} = {s['db_hit']/n*100:.1f}%")
            print(f"  重算-旧calib命中: {s['old_hit']}/{n} = {s['old_hit']/n*100:.1f}%  (λ均值 {s['old_l']/n:.2f})")
            print(f"  重算-新calib命中: {s['new_hit']}/{n} = {s['new_hit']/n*100:.1f}%  (λ均值 {s['new_l']/n:.2f})")
            print(f"  SNAP 变化的场次: {s['changed']}/{n}，其中由错变对: {s['improved']}")


if __name__ == "__main__":
    asyncio.run(main())
