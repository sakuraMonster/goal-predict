"""查询今天和明天的比赛状态"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from datetime import date
from app.db.database import async_session
from app.db.models import Match, Prediction, OddsSnapshot, HeadToHead, TeamSeasonStats
from sqlalchemy import select, func


async def main():
    today = date(2026, 8, 1)
    tomorrow = date(2026, 8, 2)

    async with async_session() as db:
        for d, label in [(today, "今天 08-01"), (tomorrow, "明天 08-02")]:
            result = await db.execute(
                select(Match).where(
                    func.date(Match.kickoff_time) == d
                ).order_by(Match.kickoff_time)
            )
            matches = list(result.scalars().all())

            print(f"\n{'='*60}")
            print(f"  {label}: {len(matches)} 场比赛")
            print(f"{'='*60}")

            for m in matches:
                # 检查球队匹配
                has_team = m.home_team_id is not None and m.away_team_id is not None
                home_name = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
                away_name = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"

                # 检查赔率
                odds_count = await db.scalar(
                    select(func.count(OddsSnapshot.id)).where(OddsSnapshot.match_id == m.id)
                ) or 0

                # 检查预测
                pred = await db.scalar(
                    select(Prediction).where(Prediction.match_id == m.id)
                )

                # 检查H2H
                h2h_count = 0
                if has_team:
                    h2h_count = await db.scalar(
                        select(func.count(HeadToHead.id)).where(
                            ((HeadToHead.home_team_id == m.home_team_id) & (HeadToHead.away_team_id == m.away_team_id)) |
                            ((HeadToHead.home_team_id == m.away_team_id) & (HeadToHead.away_team_id == m.home_team_id))
                        )
                    ) or 0

                # 检查球队赛季统计
                stats_count = 0
                if has_team:
                    stats_count = await db.scalar(
                        select(func.count(TeamSeasonStats.id)).where(
                            TeamSeasonStats.team_id.in_([m.home_team_id, m.away_team_id])
                        )
                    ) or 0

                team_status = "已匹配" if has_team else "缺失球队"
                odds_status = f"赔率{odds_count}条" if odds_count > 0 else "无赔率"
                pred_status = "已预测" if pred else "未预测"
                h2h_status = f"H2H{h2h_count}条" if h2h_count > 0 else "无H2H"
                stats_status = f"统计{stats_count}条" if stats_count > 0 else "无统计"

                print(f"  ID={m.id:>3} | {m.kickoff_time.strftime('%H:%M') if m.kickoff_time else '??:??'} | {home_name} vs {away_name}")
                print(f"         | {team_status} | {odds_status} | {pred_status} | {h2h_status} | {stats_status} | league={(m.league.name_zh if m.league else None) or '?'}")


if __name__ == "__main__":
    asyncio.run(main())
