"""临时探针：今天(竞彩周期 08-15 12:00 ~ 08-16 12:00) 19:00 前开赛的未开赛场次，
按 Model C 进球数把握度(_score_goal_pick 官方口径)排序，选最有把握的一场。"""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

from app.db.database import async_session
from app.db.models import Prediction, Match, League
from app.api.reports import _build_history_stats, _score_goal_pick

BEIJING_TZ = timezone(timedelta(hours=8))
DAYS = 30

TARGET_DATE = datetime(2026, 8, 15)
CUTOFF = datetime(2026, 8, 15, 19, 0)  # 七点前


async def main():
    now_bj = datetime.now(BEIJING_TZ)
    now_naive = now_bj.replace(tzinfo=None)

    target_start = TARGET_DATE.replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    target_end = (TARGET_DATE + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    hist_start = (now_bj - timedelta(days=DAYS)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    hist_end = (now_bj + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)

    async with async_session() as db:
        # 历史统计（与 /reports/goal-picks 一致）
        hist_result = await db.execute(
            select(
                League.name_zh,
                Prediction.expected_goals_c,
                Prediction.snap_top2_c,
                Prediction.actual_total_goals,
            )
            .outerjoin(League, League.id == Prediction.league_id)
            .where(
                and_(
                    Prediction.kickoff_time >= hist_start,
                    Prediction.kickoff_time < hist_end,
                    Prediction.actual_total_goals.isnot(None),
                    Prediction.snap_top2_c.isnot(None),
                )
            )
        )
        hist = _build_history_stats(hist_result.all())

        # 候选：今天 12:00 ~ 明天 12:00，且开赛 < 19:00，未开赛，scheduled
        match_result = await db.execute(
            select(Prediction)
            .join(Match, Match.id == Prediction.match_id)
            .options(
                joinedload(Prediction.match).joinedload(Match.home_team),
                joinedload(Prediction.match).joinedload(Match.away_team),
                joinedload(Prediction.match).joinedload(Match.league),
            )
            .where(
                and_(
                    Prediction.kickoff_time >= target_start,
                    Prediction.kickoff_time < target_end,
                    Prediction.kickoff_time < CUTOFF,
                    Prediction.snap_top2_c.isnot(None),
                    Match.status == "scheduled",
                    Match.kickoff_time >= now_naive,
                )
            )
        )
        predictions = match_result.unique().scalars().all()

        print(f"候选：今天 <19:00 未开赛场次 {len(predictions)} 场\n")
        print(f"全局近{DAYS}天命中 {hist['global']['acc']}% (n={hist['global']['n']})\n")

        scored = []
        for pred in predictions:
            match = pred.match
            home_team = match.home_team.name_zh if match.home_team else (match.home_team_name or "")
            away_team = match.away_team.name_zh if match.away_team else (match.away_team_name or "")
            league_name = match.league.name_zh if match.league else "未知联赛"
            score, signals = _score_goal_pick(
                pred.expected_goals_c,
                pred.snap_top2_c,
                league_name,
                hist,
            )
            scored.append({
                "match_num": match.match_num or "",
                "league": league_name,
                "home": home_team,
                "away": away_team,
                "kickoff": str(match.kickoff_time),
                "egc": round(pred.expected_goals_c or 0, 2),
                "snap": pred.snap_top2_c,
                "score": round(score, 1),
                "signals": signals,
            })

        scored.sort(key=lambda x: (-x["score"], x["kickoff"]))

        for i, s in enumerate(scored, 1):
            sig = " | ".join(f"{x['label']}:{x['acc']}%(n={x['n']})" for x in s["signals"])
            print(f"[{i}] {s['match_num']} {s['league']} {s['home']} vs {s['away']} {s['kickoff'][11:16]}")
            print(f"    λ={s['egc']} SNAP={s['snap']} 把握度={s['score']}")
            print(f"    信号: {sig}\n")


asyncio.run(main())
