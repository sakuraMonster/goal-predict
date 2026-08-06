"""
特征工程数据基类：从数据库提取基础数据的查询方法

从 FeatureEngineer（features.py）中抽离出的无状态数据库查询方法，
供特征工程和其他模块复用。
"""
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, or_
from app.db.models import Match, TeamSeasonStats, HeadToHead, OddsSnapshot, Injury


class BaseDataFetcher:
    """基础数据查询器：封装所有数据库查询逻辑"""

    # 联赛基线缓存：{league_id: {home_win_rate, avg_goals, avg_xg, ...}}
    _league_baselines: dict = {}

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── 工具方法 ──

    @staticmethod
    def _safe_mean(vals: list) -> float:
        """安全求均值，空列表返回 0"""
        vals = [v for v in vals if v is not None]
        return float(np.mean(vals)) if vals else 0.0

    @staticmethod
    def _safe_std(vals: list) -> float:
        """安全求标准差"""
        vals = [v for v in vals if v is not None]
        return float(np.std(vals)) if len(vals) >= 2 else 0.0

    # ── recent_matches JSON 兼容解析 ──

    @staticmethod
    def _parse_recent_score(score_str: str):
        """
        兼容两种比分分隔符：冒号 ":" 和短横线 "-"
        返回 (goals_for, goals_against)，解析失败返回 (0, 0)
        """
        if not score_str or not isinstance(score_str, str):
            return 0, 0
        for sep in (":", "-"):
            if sep in score_str:
                parts = score_str.split(sep)
                try:
                    return int(parts[0]), int(parts[1])
                except (ValueError, IndexError):
                    return 0, 0
        return 0, 0

    @staticmethod
    def _parse_recent_is_home(match_dict: dict) -> bool:
        """
        兼容两种场地字段：is_home（布尔）和 venue（"H"/"A" 字符串）
        """
        if "is_home" in match_dict and match_dict["is_home"] is not None:
            return bool(match_dict["is_home"])
        venue = match_dict.get("venue", "")
        if isinstance(venue, str):
            return venue.upper() == "H"
        return False

    # ── 数据查询方法 ──

    async def _get_match(self, match_id: int):
        result = await self.db.execute(select(Match).where(Match.id == match_id))
        return result.scalar_one_or_none()

    async def _get_rest_days(self, team_id: int, current_time) -> float:
        """查询球队上一场比赛距当前比赛的天数"""
        if not team_id or not current_time:
            return 7.0
        result = await self.db.execute(
            select(Match.kickoff_time).where(
                Match.home_team_id == team_id,
                Match.kickoff_time < current_time
            ).union(
                select(Match.kickoff_time).where(
                    Match.away_team_id == team_id,
                    Match.kickoff_time < current_time
                )
            ).order_by(Match.kickoff_time.desc()).limit(1)
        )
        row = result.first()
        if not row or not row[0]:
            return 7.0
        last_time = row[0]
        if hasattr(last_time, 'tzinfo') and last_time.tzinfo is not None:
            last_time = last_time.replace(tzinfo=None)
        delta = current_time - last_time
        return max(0.0, delta.total_seconds() / 86400.0)

    @staticmethod
    def _is_stats_valid(stats: TeamSeasonStats) -> bool:
        """校验赛季统计数据是否合理，过滤 SportMonks 返回的腐败数据
        （如 played=1 但 GA=59 / GF=67，实际是赛季累计值误标为单场）"""
        p = stats.played
        if not p or p <= 0:
            return False
        # W+D+L 应与 played 大致相等（允许±25%容差，考虑加时赛等特殊情况）
        wdl = (stats.wins or 0) + (stats.draws or 0) + (stats.losses or 0)
        if wdl > p * 1.25:
            return False
        # 场均进球/失球不应超过 8.0
        if p > 0:
            if (stats.goals_for or 0) / p > 8.0:
                return False
            if (stats.goals_against or 0) / p > 8.0:
                return False
        return True

    async def _get_team_stats(self, team_id: int):
        """获取球队赛季统计，优先完整数据 + 赛季数值最大的记录，自动过滤腐败数据"""
        if not team_id:
            return None
        # 优先选择 played>0 且 recent_matches 非空的记录
        # 按 played DESC（更多比赛优先），确保真实赛季排在 SportMonks ID 前
        result = await self.db.execute(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team_id,
                TeamSeasonStats.played > 0,
                TeamSeasonStats.recent_matches.isnot(None),
            ).order_by(TeamSeasonStats.played.desc(), TeamSeasonStats.id.desc())
        )
        for stats in result.scalars().all():
            if self._is_stats_valid(stats):
                rm = stats.recent_matches
                if isinstance(rm, list) and len(rm) > 0:
                    return stats
                # 否则 recent_matches 为空列表，继续找下一条（played 多的记录可能 rm 为空）

        # 回退：放宽 recent_matches 条件，但仍校验数据有效性
        result = await self.db.execute(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team_id,
                TeamSeasonStats.played > 0,
            ).order_by(TeamSeasonStats.played.desc(), TeamSeasonStats.id.desc())
        )
        for stats in result.scalars().all():
            if self._is_stats_valid(stats):
                return stats

        # 最终回退：返回最新记录（即使可能无效，让特征提取层做兜底）
        result = await self.db.execute(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team_id
            ).order_by(TeamSeasonStats.id.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_h2h(self, team1_id: int, team2_id: int, before_date=None) -> list:
        """获取历史交锋记录，可选过滤 before_date 及之后的比赛（防数据泄露，按日期比较）
        before_date 是北京时间，需减去 8 小时转 UTC 后再比较日期"""
        if not team1_id or not team2_id:
            return []
        from sqlalchemy import func as sa_func
        from datetime import timedelta
        query = select(HeadToHead).where(
            ((HeadToHead.home_team_id == team1_id) & (HeadToHead.away_team_id == team2_id)) |
            ((HeadToHead.home_team_id == team2_id) & (HeadToHead.away_team_id == team1_id))
        )
        if before_date:
            from datetime import timedelta, date as dt_date
            utc_date = before_date - timedelta(hours=8) if hasattr(before_date, 'strftime') else before_date
            cutoff_date = utc_date.date() if hasattr(utc_date, 'date') else dt_date.fromisoformat(str(utc_date)[:10])
            query = query.where(sa_func.date(HeadToHead.match_date) < cutoff_date)
        query = query.order_by(HeadToHead.match_date.desc()).limit(10)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def _get_odds_structured(self, match_id: int) -> dict:
        """V4: 按博彩公司分组获取结构化赔率数据

        返回 dict:
          - has_data: bool
          - by_bookmaker: {bookmaker_name: [OddsSnapshot]}  按时间升序
          - times: 去重排序的时间点列表
          - latest: 最新时间点的所有博彩公司快照
          - prev: 次新时间点的所有博彩公司快照（无则为 []）
          - bookmaker_count: 博彩公司数量
          - time_count: 时间点数量
        """
        result = await self.db.execute(
            select(OddsSnapshot).where(
                OddsSnapshot.match_id == match_id
            ).order_by(OddsSnapshot.snapshot_time.asc())
        )
        all_odds = list(result.scalars().all())

        if not all_odds:
            return {"has_data": False}

        # 按博彩公司分组（时间升序）
        by_bookmaker: dict[str, list] = {}
        for o in all_odds:
            bm = o.bookmaker or "unknown"
            by_bookmaker.setdefault(bm, []).append(o)

        # 去重时间点
        times = sorted(set(o.snapshot_time for o in all_odds))

        # 最新 / 次新时间点的快照
        latest_time = times[-1]
        latest = [o for o in all_odds if o.snapshot_time == latest_time]
        prev = []
        if len(times) >= 2:
            prev_time = times[-2]
            prev = [o for o in all_odds if o.snapshot_time == prev_time]

        return {
            "has_data": True,
            "by_bookmaker": by_bookmaker,
            "times": times,
            "latest": latest,
            "prev": prev,
            "bookmaker_count": len(by_bookmaker),
            "time_count": len(times),
        }

    async def _get_injury_count(self, team_id: int) -> int:
        if not team_id:
            return 0
        result = await self.db.execute(
            select(Injury).where(Injury.team_id == team_id, Injury.status == "out")
        )
        return len(list(result.scalars().all()))

    async def _get_league_baseline(self, league_id: int) -> dict:
        """V4: 计算联赛基线统计（带缓存），用于跨联赛特征归一化"""
        # 全局默认基线：联赛不可用时用典型值代替，避免 StandardScaler 将 0 映射为极端负值
        FALLBACK_BASELINE = {
            "league_count": 0,
            "league_avg_home_goals": 1.5,
            "league_avg_away_goals": 1.2,
            "league_home_win_rate": 0.45,
            "league_draw_rate": 0.25,
            "league_avg_total_goals": 2.7,
        }
        if not league_id:
            return FALLBACK_BASELINE
        if league_id in BaseDataFetcher._league_baselines:
            return BaseDataFetcher._league_baselines[league_id]

        # 查询该联赛所有已完成比赛
        result = await self.db.execute(
            select(
                func.count(Match.id),
                func.avg(Match.home_score),
                func.avg(Match.away_score),
                func.avg(case((Match.home_score > Match.away_score, 1), else_=0)),
                func.avg(case((Match.home_score == Match.away_score, 1), else_=0)),
            ).where(
                Match.league_id == league_id,
                Match.home_score.isnot(None),
                Match.status == "finished",
            )
        )
        row = result.one()
        count = row[0] or 0
        if count < 10:
            baseline = dict(FALLBACK_BASELINE)  # 数据太少，用全局默认值
        else:
            baseline = {
                "league_count": count,
                "league_avg_home_goals": float(row[1] or 0),
                "league_avg_away_goals": float(row[2] or 0),
                "league_home_win_rate": float(row[3] or 0),
                "league_draw_rate": float(row[4] or 0),
                "league_avg_total_goals": float((row[1] or 0) + (row[2] or 0)),
            }

        BaseDataFetcher._league_baselines[league_id] = baseline
        return baseline

    async def _get_uefa_experience(self, team_id: int) -> dict:
        """V4: 查询球队欧战历史经验（从已完成比赛中筛选 UEFA 赛事）"""
        uefa_keywords = ["UEFA", "Champions", "Europa", "Conference", "欧冠", "欧罗巴", "欧协联"]
        result = await self.db.execute(
            select(
                func.count(Match.id),
                func.sum(case((Match.home_score > Match.away_score, 1), else_=0)),
                func.sum(case((Match.home_score == Match.away_score, 1), else_=0)),
                func.sum(Match.home_score),
                func.sum(Match.away_score),
            ).where(
                or_(
                    Match.home_team_id == team_id,
                    Match.away_team_id == team_id,
                ),
                Match.status == "finished",
                Match.home_score.isnot(None),
                or_(*[Match.venue.ilike(f"%{kw}%") for kw in uefa_keywords]),
            )
        )
        row = result.one()
        count = row[0] or 0
        return {
            "uefa_matches": count,
            "uefa_wins": int(row[1] or 0),
            "uefa_draws": int(row[2] or 0),
            "uefa_goals_for": int(row[3] or 0),
            "uefa_goals_against": int(row[4] or 0),
        }
