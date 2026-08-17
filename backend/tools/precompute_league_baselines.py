"""预计算联赛历史赛季基线（L1） + calib 建议（L4）

数据源：team_season_stats 中完整赛季（默认 season='2025'）的球队统计，
经 teams 表反查真实 league_id（team_season_stats.league_id 存在污染，不可直接使用）。

聚合口径（与 features_base._get_league_baseline 返回结构一致）：
  sum(played) = 各队 played 之和 = 队次总和（每场比赛计入 2 个队次）
  league_avg_total_goals = 2 * sum(goals_for) / sum(played)   # 场均总进球
  league_home_win_rate   = 2 * sum(home_wins) / sum(played)   # 主队获胜场次 / 总场次
  league_draw_rate       = sum(draws) / sum(played)           # 平局场次占比（每场平局两队各记一次，比例恰好正确，无需×2）
  league_avg_home_goals  = league_avg_away_goals = avg_total/2  # 无主客场进球分列字段，近似取半

calib 建议（L4）：calib = league_avg_total_goals / 2.5（与 model_c.LEAGUE_PARAMS 现有口径一致）

用法：
  python -m tools.precompute_league_baselines            # 用 season='2025' 预计算并写入
  python -m tools.precompute_league_baselines 2025       # 指定赛季
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func
from app.db.database import engine, Base
from app.db.models import LeagueSeasonBaseline, TeamSeasonStats, League, Team
from app.predictor.models.model_c import ModelC

OUT = os.path.join(os.path.dirname(__file__), "league_baselines_report.txt")

MIN_PLAYED = 10          # 少于 10 场的赛季记录视为不完整，不参与聚合
STANDARD_GL = 2.5        # calib 计算的标准盘口线（与 LEAGUE_PARAMS 注释口径一致）


def _stats_valid(stats) -> bool:
    """过滤腐败赛季记录（与 features_base._is_stats_valid 同源口径）"""
    p = stats.played or 0
    if p < MIN_PLAYED:
        return False
    wdl = (stats.wins or 0) + (stats.draws or 0) + (stats.losses or 0)
    if wdl > p * 1.25:
        return False
    if (stats.goals_for or 0) / p > 8.0 or (stats.goals_against or 0) / p > 8.0:
        return False
    # 主客场分项之和不应超过总场次（+容差）
    h = (stats.home_wins or 0) + (stats.home_draws or 0) + (stats.home_losses or 0)
    a = (stats.away_wins or 0) + (stats.away_draws or 0) + (stats.away_losses or 0)
    if h + a > p * 1.25:
        return False
    return True


async def main():
    season = sys.argv[1] if len(sys.argv) > 1 else "2025"

    # 确保新表存在（create_all 只补建不存在的表）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.db.database import async_session
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        # 清空该赛季旧数据，重新预计算（保证可重复执行）
        await db.execute(
            LeagueSeasonBaseline.__table__.delete().where(LeagueSeasonBaseline.season == season)
        )

        # 所有有球队记录的联赛
        r = await db.execute(
            select(League.id, League.name_zh, League.name_en)
            .join(Team, Team.league_id == League.id)
            .distinct()
            .order_by(League.id)
        )
        leagues = r.all()

        # 该赛季全部球队统计（经 Team 反查真实 league_id）
        r = await db.execute(
            select(TeamSeasonStats, Team.league_id)
            .join(Team, Team.id == TeamSeasonStats.team_id)
            .where(TeamSeasonStats.season == season)
        )
        stats_by_league: dict[int, list] = {}
        for stats, real_league_id in r.all():
            if real_league_id and _stats_valid(stats):
                stats_by_league.setdefault(real_league_id, []).append(stats)

        existing_params = ModelC.LEAGUE_PARAMS
        p(f"=== 联赛历史赛季基线预计算（season={season}） ===")
        p(f"标准盘口线 STANDARD_GL = {STANDARD_GL}")
        p("")

        calib_suggestions = []
        written = 0
        for lg_id, name_zh, name_en in leagues:
            stats_list = stats_by_league.get(lg_id, [])
            team_cnt = len(stats_list)
            if team_cnt < 6:
                p(f"[跳过] {name_zh}({name_en}) id={lg_id}: 有效球队统计仅 {team_cnt} 支（<6），数据不足")
                continue

            sum_played = sum(s.played for s in stats_list)
            sum_gf = sum(s.goals_for or 0 for s in stats_list)
            sum_home_wins = sum(s.home_wins or 0 for s in stats_list)
            sum_draws = sum(s.draws or 0 for s in stats_list)
            if sum_played <= 0:
                continue

            avg_total = 2 * sum_gf / sum_played
            home_win_rate = 2 * sum_home_wins / sum_played
            draw_rate = sum_draws / sum_played
            calib_suggested = avg_total / STANDARD_GL

            baseline = LeagueSeasonBaseline(
                league_id=lg_id,
                season=season,
                league_count=team_cnt,
                league_avg_home_goals=round(avg_total / 2, 4),
                league_avg_away_goals=round(avg_total / 2, 4),
                league_home_win_rate=round(home_win_rate, 4),
                league_draw_rate=round(draw_rate, 4),
                league_avg_total_goals=round(avg_total, 4),
                source=f"team_season_stats season={season} 聚合(球队数={team_cnt}, 场次数={sum_played})",
            )
            db.add(baseline)
            written += 1

            # 对比现有 LEAGUE_PARAMS calib
            cur = existing_params.get(name_zh, {}).get("calib")
            if cur is None:
                cur = existing_params["default"]["calib"]
            status = "已有" if name_zh in existing_params and "calib" in existing_params.get(name_zh, {}) else "缺失"
            calib_suggestions.append((name_zh, lg_id, avg_total, calib_suggested, cur, status))

            p(f"[写入] {name_zh}({name_en}) id={lg_id}: 球队={team_cnt} 场次={sum_played} "
              f"场均总进球={avg_total:.3f} 主胜率={home_win_rate:.3f} 平局率={draw_rate:.3f}")

        await db.commit()

        p("")
        p("=== calib 建议表（L4：avg_total / 2.5，与 LEAGUE_PARAMS 口径一致） ===")
        p(f"{'联赛':<10}{'id':<6}{'场均总进球':<12}{'calib建议':<10}{'现有calib':<10}状态")
        for name, lg_id, avg_total, sug, cur, status in sorted(calib_suggestions, key=lambda x: x[1]):
            p(f"{name:<10}{lg_id:<6}{avg_total:<12.3f}{sug:<10.3f}{cur:<10.3f}{status}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"写入基线 {written} 条，报告输出: {OUT}")


asyncio.run(main())
