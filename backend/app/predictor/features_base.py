"""
特征工程数据基类：从数据库提取基础数据的查询方法

从 FeatureEngineer（features.py）中抽离出的无状态数据库查询方法，
供特征工程和其他模块复用。
"""
import numpy as np
from collections import Counter
from datetime import timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, or_
from app.db.models import Match, TeamSeasonStats, HeadToHead, OddsSnapshot, Injury, LeagueSeasonBaseline, Team
from app import ou_flags


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

    @staticmethod
    def _season_rank(stats) -> int:
        """赛季分层优先级：
        2 = 4位数字年份赛季标记（'2025'/'2026'，真实赛季）
        1 = SportMonks 数字赛季ID（'26741' 等，多为不完整/脏数据）
        0 = 'latest' 等非标准标记（最低）
        """
        s = stats.season
        if s and s.isdigit() and len(s) == 4:
            return 2
        if s == "latest":
            return 0
        return 1

    async def _filter_cross_league_rm(self, stats_list: list, team_id: int, league_id: int = None) -> list:
        """过滤 recent_matches 明显串台的记录（对手联赛与本队不一致占比过高）

        recent_matches 正常应包含本联赛对手（杯赛对手可跨联赛但占比低）；
        串台记录（如波尔图 recent 全是英格兰业余队）对本队无效。
        阈值：可判别联赛的对手中，同联赛占比 < 30% 判定串台。

        L2.1(2026-08-10) 修正：league_id 由调用方从 match.league_id 传入
        （matches 表联赛归属干净），不再反查 teams.league_id ——
        该字段被批量写入系统性污染（1190 队被标成韩K=6）。
        """
        my_league = league_id
        if not my_league or not stats_list:
            return stats_list

        # 收集对手 sportmonks_id
        opp_ids = set()
        for s in stats_list:
            rm = s.recent_matches
            if isinstance(rm, list):
                for m in rm:
                    if isinstance(m, dict) and m.get("opponent_sm_id"):
                        opp_ids.add(m["opponent_sm_id"])
        if not opp_ids:
            return stats_list

        # 一次查询所有对手的联赛归属
        r = await self.db.execute(
            select(Team.sportmonks_id, Team.league_id).where(Team.sportmonks_id.in_(opp_ids))
        )
        opp_league = {sm_id: lg_id for sm_id, lg_id in r.all()}

        kept = []
        for s in stats_list:
            rm = s.recent_matches
            if not isinstance(rm, list) or not rm:
                kept.append(s)  # 无 rm 无法判定，保留
                continue
            same = total = 0
            for m in rm:
                if not isinstance(m, dict):
                    continue
                lg = opp_league.get(m.get("opponent_sm_id"))
                if lg is not None:
                    total += 1
                    if lg == my_league:
                        same += 1
            if total == 0 or (same / total) >= 0.3:
                kept.append(s)
        return kept

    async def _get_team_stats(self, team_id: int, league_id: int = None):
        """获取球队赛季统计

        L2(2026-08-10) 治理：赛季分层 + 串台过滤 + 近况优先，避免新赛季前期选中垃圾记录：
        1. 优先 4位年份赛季（'2025'/'2026'）记录，其次 SportMonks 数字ID，最后 'latest'
        2. 过滤 recent_matches 跨联赛串台记录（league_id 来自 match.league_id，干净）
        3. 同层内：recent_matches 非空优先（保证近6场特征有值），再按 played DESC
        L2.1(2026-08-10) 修正：串台过滤后池为空 → 返回 None（宁缺毋滥，
        特征层走 league baseline fallback），不再回退到未过滤的垃圾记录。
        """
        if not team_id:
            return None
        result = await self.db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.team_id == team_id)
        )
        all_stats = list(result.scalars().all())
        if not all_stats:
            return None

        valid_stats = [s for s in all_stats if self._is_stats_valid(s)]
        if not valid_stats:
            # 全部无效：返回最新记录兜底（特征层有 fallback）
            return all_stats[0]

        # 存在合法年份赛季记录时，仅在其中选择（否则放宽到全部有效记录）
        year_stats = [s for s in valid_stats if self._season_rank(s) == 2]
        pool = year_stats if year_stats else valid_stats

        # 串台过滤（仅对有 rm 的记录有效，无 rm 记录保留）
        pool = await self._filter_cross_league_rm(pool, team_id, league_id)
        if not pool:
            return None  # 全部串台/无法判定的记录被滤除 → 走 baseline fallback

        # 排序：rm 非空优先 → played DESC
        def _rm_nonempty(s):
            rm = s.recent_matches
            return 1 if (isinstance(rm, list) and len(rm) > 0) else 0

        pool.sort(
            key=lambda s: (_rm_nonempty(s), s.played or 0, s.id),
            reverse=True,
        )
        return pool[0]

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
        if count >= 10:
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

        # ── 第二级降级：历史赛季基线表（L1） ──
        # 新赛季前期实时比赛不足（count<10）或比赛结果未回写时，
        # 用 2025-26 完整赛季聚合基线兜底（tools/precompute_league_baselines.py 预计算）
        hist_result = await self.db.execute(
            select(LeagueSeasonBaseline).where(
                LeagueSeasonBaseline.league_id == league_id
            ).order_by(LeagueSeasonBaseline.season.desc(), LeagueSeasonBaseline.id.desc()).limit(1)
        )
        hist = hist_result.scalar_one_or_none()
        if hist is not None and (hist.league_avg_total_goals or 0) > 0:
            baseline = {
                "league_count": hist.league_count or 0,
                "league_avg_home_goals": float(hist.league_avg_home_goals or 0),
                "league_avg_away_goals": float(hist.league_avg_away_goals or 0),
                "league_home_win_rate": float(hist.league_home_win_rate or 0),
                "league_draw_rate": float(hist.league_draw_rate or 0),
                "league_avg_total_goals": float(hist.league_avg_total_goals or 0),
            }
            BaseDataFetcher._league_baselines[league_id] = baseline
            return baseline

        # ── 第三级降级：全局默认 ──
        baseline = dict(FALLBACK_BASELINE)  # 数据太少，用全局默认值
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

    # ═══════════════════════════════════════════════════════════════
    # 共享 OU 特征计算（新算法：加权众数 + 双维度回落）
    # ═══════════════════════════════════════════════════════════════

    # ── 联赛感知常量 ──
    LEAGUE_OU_RANGES: dict[str, tuple[float, float]] = {
        "韩K":   (1.75, 3.0),
        "韩K2":  (1.75, 3.0),
        "日职":   (1.75, 3.25),
        "日乙":   (1.75, 3.0),
        "挪超":   (2.25, 3.75),
        "瑞典超": (2.0,  3.25),
        "芬超":   (2.0,  3.0),
        "美职联": (2.0,  3.5),
        "荷乙":   (2.25, 3.75),
        "荷甲":   (2.25, 3.75),
        "德乙":   (2.0,  3.5),
        "法乙":   (1.5,  2.75),
        "澳超":   (2.25, 3.75),
        "墨超":   (2.0,  3.25),
        "巴甲":   (1.75, 3.0),
    }

    LEAGUE_FALLBACK_GL: dict[str, float] = {
        "韩K": 2.25, "韩K2": 2.25, "日职": 2.5, "日乙": 2.25,
        "挪超": 2.75, "瑞典超": 2.5, "芬超": 2.5, "美职联": 2.75,
        "荷乙": 3.0, "荷甲": 2.75, "德乙": 2.75, "法乙": 2.0,
        "澳超": 2.75, "墨超": 2.5, "巴甲": 2.25,
    }

    UNIVERSAL_OU_MIN = 1.5
    UNIVERSAL_OU_MAX = 3.5
    OU_GOAL_LINE_MIN_COUNT = 2
    OU_ODDS_MIN_SAMPLES = 2
    OU_DECLINE_WINDOW_HOURS = 6
    OU_DECLINE_MIN_HOURS = 0.5
    # v2 (OU_OPENING_ANCHOR): 开盘锚定 + 稳健信号
    OU_CURRENT_GL_WINDOW = 3          # 近窗口共识线：最近 N 个快照时刻
    OU_LOW_SOURCE_THRESHOLD = 3       # 有效数据源低于该值 → 低置信降级
    # 2026-08-11 实证: ×0.5 折扣导致全量145场 2 场命中损失(78→80)，降级组件净损失，
    # 信号本身携带价值（研究结论 +7~10pp），故默认 1.0（不折扣）；保留旋钮可调。
    OU_LOW_SOURCE_SIGNAL_SCALE = 1.0

    # ── 特征默认值 ──
    OU_DEFAULT_VAL = 0.0
    RANK_DEFAULT_POS = 0

    @staticmethod
    def _ou_main_line_consensus(records: list) -> tuple:
        """从 (gl, ov, un, bm) 记录计算主盘线共识。

        每家博彩公司先选"主盘线"（|over_odds - under_odds| 最小 = 盘口最均衡），
        再对各家主盘线取众数；众数覆盖率 ≥ 0.4 采纳众数，否则取中位数。
        返回 (consensus_gl, 有效博彩公司数)；无有效数据返回 (None, 0)。
        """
        bm_main: dict = {}
        for gl, ov, un, bm in records:
            if ov is None or un is None:
                continue
            diff = abs(ov - un)
            if bm not in bm_main or diff < bm_main[bm][1]:
                bm_main[bm] = (gl, diff)
        gls = [v[0] for v in bm_main.values()]
        if not gls:
            return None, 0
        counter = Counter(gls)
        cand, cnt = counter.most_common(1)[0]
        if cnt / len(gls) >= 0.4:
            return cand, len(gls)
        sorted_gls = sorted(gls)
        return sorted_gls[len(sorted_gls) // 2], len(gls)

    def _compute_ou_features(self, all_odds_list: list, times: list, by_bm: dict,
                              latest: list, league_name: str = None) -> dict:
        """共享的大小球特征计算（V5: 加权众数 + 双维度回落 + 联赛感知）

        返回 dict 包含新旧两套特征：
          - 新特征: goal_line_market, odds_drift_over_mean, goal_line_shift, ...
          - 旧对照: goal_line_market_old, goal_line_drop_from_peak_old, ...
        """
        from collections import Counter
        from datetime import timedelta

        feats = {
            # 新特征
            "goal_line_market": self.OU_DEFAULT_VAL,
            "over_odds_current": self.OU_DEFAULT_VAL,
            "under_odds_current": self.OU_DEFAULT_VAL,
            "over_odds_movement": self.OU_DEFAULT_VAL,
            "under_odds_movement": self.OU_DEFAULT_VAL,
            "goal_line_change": self.OU_DEFAULT_VAL,
            "goal_line_drop_from_peak": self.OU_DEFAULT_VAL,  # 新语义：整线位移
            "goal_line_volatility": self.OU_DEFAULT_VAL,
            "over_odds_decline_rate": self.OU_DEFAULT_VAL,
            "odds_drift_over_mean": self.OU_DEFAULT_VAL,       # 新：同线水位漂移均值
            "odds_drift_consensus": self.OU_DEFAULT_VAL,       # 新：漂移方向一致性
            "goal_line_shift": self.OU_DEFAULT_VAL,            # 新：整线位移
            # 旧对照特征
            "goal_line_market_old": self.OU_DEFAULT_VAL,
            "goal_line_drop_from_peak_old": self.OU_DEFAULT_VAL,
            "goal_line_max_old": self.OU_DEFAULT_VAL,
            "goal_line_min_old": self.OU_DEFAULT_VAL,
        }

        # ── Step 0: 数据准备 —— 按 (时间, bookmaker, goal_line) 去重 + 精度统一 ──
        ou_records: dict[tuple, tuple] = {}
        for t in times:
            snaps_at_t = [o for o in all_odds_list if o.snapshot_time == t]
            for o in snaps_at_t:
                if o.goal_line is None or (o.over_odds is None and o.under_odds is None):
                    continue
                gl = round(float(o.goal_line), 2)
                key = (t, o.bookmaker, gl)
                if key not in ou_records:
                    ou_records[key] = (o.over_odds, o.under_odds)

        if not ou_records:
            return feats

        all_ou_times = [
            (t, gl, ov, un, bm)
            for (t, bm, gl), (ov, un) in ou_records.items()
        ]

        # ── Step 1: 动态过滤 —— 联赛感知范围 + 通用兜底 ──
        def _get_ou_filter_range(ln):
            if ln and ln in self.LEAGUE_OU_RANGES:
                return self.LEAGUE_OU_RANGES[ln]
            return self.UNIVERSAL_OU_MIN, self.UNIVERSAL_OU_MAX

        league_min, league_max = _get_ou_filter_range(league_name)
        valid_ou_times = [
            x for x in all_ou_times
            if league_min <= x[1] <= league_max
        ]
        if not valid_ou_times and (league_min != self.UNIVERSAL_OU_MIN or league_max != self.UNIVERSAL_OU_MAX):
            valid_ou_times = [
                x for x in all_ou_times
                if self.UNIVERSAL_OU_MIN <= x[1] <= self.UNIVERSAL_OU_MAX
            ]

        ou_times_sorted = sorted(set(t for (t, gl, ov, un, bm) in all_ou_times))
        latest_t = ou_times_sorted[-1] if ou_times_sorted else None

        # ── Step 2: 基准线确定 —— 加权众数 + 覆盖率阈值 + 联赛感知兜底 ──
        # v2 (OU_OPENING_ANCHOR): goal_line_market 锚定开盘线（第一个能算主盘线的时刻），
        #   消除随快照批次累积导致最新共识线横跳（15578 案例）；盘口变动信息收敛到
        #   goal_line_shift（开盘线 vs 近窗口共识线）。
        best_gl = None
        best_gl_old = None   # 旧算法对照
        opening_gl = None    # v2: 开盘共识线
        opening_bm_count = 0  # v2: 开盘有效数据源数（低源降级用）
        current_gl = None    # v2: 近窗口平滑共识线（位移信号用）

        if latest_t and all_ou_times:
            # 2a. 新算法：每家博彩公司先选出"主盘线"（over/under 赔率差值最小 = 盘口最均衡）
            #     → 再对各家主盘线取加权众数
            latest_records = [
                (gl, ov, un, bm) for (t, gl, ov, un, bm) in valid_ou_times if t == latest_t
            ]
            current_gl, current_bm = (
                self._ou_main_line_consensus(latest_records) if latest_records else (None, 0)
            )
            if current_bm < self.OU_GOAL_LINE_MIN_COUNT:
                current_gl = None

            if ou_flags.OU_OPENING_ANCHOR:
                # ── v2 开盘锚定 ──
                # 开盘线：第一个 over+under 齐全、能算主盘线的时刻（数据源 ≥ 最低共识数）
                for opening_t in ou_times_sorted:
                    opening_records = [
                        (gl, ov, un, bm) for (t, gl, ov, un, bm) in valid_ou_times if t == opening_t
                    ]
                    cand, n_bm = self._ou_main_line_consensus(opening_records)
                    if cand is not None and n_bm >= self.OU_GOAL_LINE_MIN_COUNT:
                        opening_gl = cand
                        opening_bm_count = n_bm
                        break
                # 近窗口平滑共识线（最近 OU_CURRENT_GL_WINDOW 个时刻，逐批共识取众数，
                # 并列时取最新批次值——抗单批噪声，同时不过度滞后）
                window_consensus = []
                for wt in ou_times_sorted[-self.OU_CURRENT_GL_WINDOW:]:
                    wrecs = [
                        (gl, ov, un, bm) for (t, gl, ov, un, bm) in valid_ou_times if t == wt
                    ]
                    cand, n_bm = self._ou_main_line_consensus(wrecs)
                    if cand is not None and n_bm >= self.OU_GOAL_LINE_MIN_COUNT:
                        window_consensus.append(cand)
                if window_consensus:
                    wc_counter = Counter(window_consensus)
                    max_c = max(wc_counter.values())
                    current_gl = next(
                        v for v in reversed(window_consensus) if wc_counter[v] == max_c
                    )
                if current_gl is None:
                    current_gl = opening_gl
                # 锚定：开盘线优先，缺失时退化为近窗口线
                best_gl = opening_gl if opening_gl is not None else current_gl
            else:
                # ── 旧逻辑：最新批次共识即基准线 ──
                best_gl = current_gl

            # 2b. 旧算法对照：全局 valid 快照的众数
            all_valid_gls = [gl for (t, gl, ov, un, bm) in valid_ou_times]
            if all_valid_gls:
                gl_counter_old = Counter(all_valid_gls)
                best_gl_old = gl_counter_old.most_common(1)[0][0]
                feats["goal_line_max_old"] = max(all_valid_gls)
                feats["goal_line_min_old"] = min(all_valid_gls)
                if best_gl_old and feats["goal_line_max_old"] > 0:
                    feats["goal_line_drop_from_peak_old"] = feats["goal_line_max_old"] - float(best_gl_old)

            # 兜底
            if not best_gl:
                if all_valid_gls:
                    gl_counter_all = Counter(all_valid_gls)
                    best_gl = gl_counter_all.most_common(1)[0][0]
                else:
                    best_gl = None

        # ── 联赛感知兜底 ──
        if best_gl:
            feats["goal_line_market"] = float(best_gl)
        else:
            feats["goal_line_market"] = float(
                self.LEAGUE_FALLBACK_GL.get(league_name, 2.5)
            )
        feats["goal_line_market_old"] = float(best_gl_old or best_gl or self.LEAGUE_FALLBACK_GL.get(league_name, 2.5))

        # ── Step 3: 回落信号 —— 双维度 ──
        if best_gl and all_ou_times:
            # 维度 A: 同线水位漂移（odds drift）
            same_line_records = [
                (t, ov, un, bm) for (t, gl, ov, un, bm) in all_ou_times
                if abs(gl - best_gl) < 0.001 and ov is not None
            ]

            if same_line_records:
                bm_drift: dict[str, float] = {}
                bm_groups: dict[str, list] = {}
                for t, ov, un, bm in same_line_records:
                    bm_groups.setdefault(bm, []).append((t, ov))

                for bm, records in bm_groups.items():
                    sorted_recs = sorted(records, key=lambda x: x[0])
                    if len(sorted_recs) >= 2:
                        first_over = sorted_recs[0][1]
                        last_over = sorted_recs[-1][1]
                        bm_drift[bm] = first_over - last_over

                if bm_drift:
                    drift_values = list(bm_drift.values())
                    feats["odds_drift_over_mean"] = self._safe_mean(drift_values)
                    # v2 低源降级: 开盘有效数据源不足 → 水位漂移信号打折，更靠拢基线
                    if ou_flags.OU_OPENING_ANCHOR and opening_bm_count < self.OU_LOW_SOURCE_THRESHOLD:
                        feats["odds_drift_over_mean"] *= self.OU_LOW_SOURCE_SIGNAL_SCALE
                    feats["odds_drift_consensus"] = sum(
                        1 for d in drift_values if d > 0.01
                    ) / max(len(drift_values), 1)

                # 即时 Over/Under 水位（best_gl 线上最新）
                cur_same_line = [
                    (ov, un) for (t, ov, un, bm) in same_line_records if t == latest_t
                ]
                feats["over_odds_current"] = self._safe_mean(
                    [x[0] for x in cur_same_line if x[0] is not None]
                )
                feats["under_odds_current"] = self._safe_mean(
                    [x[1] for x in cur_same_line if x[1] is not None]
                )

            # 维度 B: 整线位移（开盘共识 → 即时共识 goal_line）
            if ou_flags.OU_OPENING_ANCHOR:
                # v2: 位移 = 开盘共识线 - 近窗口平滑共识线（窗口众数抗单批噪声）
                if opening_gl is not None and current_gl is not None:
                    shift = float(opening_gl) - float(current_gl)
                    # 低源降级: 开盘有效数据源不足 → 位移信号打折，更靠拢基线
                    if opening_bm_count < self.OU_LOW_SOURCE_THRESHOLD:
                        shift *= self.OU_LOW_SOURCE_SIGNAL_SCALE
                    feats["goal_line_shift"] = shift
            else:
                # 旧逻辑：初盘共识线 = 第一个能算出主盘线的历史时刻（跳过 OU 数据不完整的时刻）
                # 注意：历史快照的 OU 数据多为单边残线（旧采集器只取 SM 返回第一条方向，
                # 且挂载到每个 HCP 行），单边线无法判定主盘线，必须 over+under 齐全才能参与，
                # 否则会用残线污染初盘共识（实测 15550 被污染成 shift=-1.25）
                for opening_t in ou_times_sorted:
                    opening_records = [
                        (gl, ov, un, bm) for (t, gl, ov, un, bm) in valid_ou_times if t == opening_t
                    ]
                    bm_opening_lines: dict[str, tuple] = {}
                    for gl, ov, un, bm in opening_records:
                        if ov is None or un is None:
                            continue
                        diff = abs(ov - un)
                        if bm not in bm_opening_lines or diff < bm_opening_lines[bm][1]:
                            bm_opening_lines[bm] = (gl, diff)
                    if bm_opening_lines:
                        opening_gls_list = [v[0] for v in bm_opening_lines.values()]
                        opening_counter = Counter(opening_gls_list)
                        opening_gl = opening_counter.most_common(1)[0][0]
                        feats["goal_line_shift"] = float(opening_gl) - float(best_gl)
                        break

            # goal_line_drop_from_peak 改为整线位移
            feats["goal_line_drop_from_peak"] = feats["goal_line_shift"]

            # ── 波动率 ──
            all_gls = [gl for (t, gl, ov, un, bm) in valid_ou_times]
            feats["goal_line_volatility"] = self._safe_std(all_gls)

            # ── Over 赔率衰减速率（best_gl 同线追踪） ──
            if same_line_records:
                over_time_series = sorted(set(
                    (t, ov) for (t, ov, un, bm) in same_line_records
                ), key=lambda x: x[0])
                if len(over_time_series) >= self.OU_ODDS_MIN_SAMPLES:
                    cutoff = over_time_series[-1][0] - timedelta(hours=self.OU_DECLINE_WINDOW_HOURS)
                    recent = [(t, ov) for (t, ov) in over_time_series if t >= cutoff]
                    if len(recent) >= self.OU_ODDS_MIN_SAMPLES:
                        first_t, first_ov = recent[0]
                        last_t, last_ov = recent[-1]
                        hours = max(
                            (last_t - first_t).total_seconds() / 3600,
                            self.OU_DECLINE_MIN_HOURS
                        )
                        rate = (first_ov - last_ov) / hours
                        feats["over_odds_decline_rate"] = rate / first_ov if first_ov > 0 else rate

            # ── over_odds_movement / under_odds_movement / goal_line_change ──
            over_moves, under_moves, gl_moves = [], [], []
            for bm, snaps in by_bm.items():
                ou_snaps = [
                    (s.snapshot_time, round(float(s.goal_line), 2), s.over_odds, s.under_odds)
                    for s in snaps if s.goal_line is not None
                    and (s.over_odds is not None or s.under_odds is not None)
                ]
                # 去重
                seen = set()
                ou_snaps_dedup = []
                for t_s, gl_s, ov_s, un_s in ou_snaps:
                    key = (t_s, gl_s)
                    if key not in seen:
                        seen.add(key)
                        ou_snaps_dedup.append((t_s, gl_s, ov_s, un_s))
                ou_snaps = ou_snaps_dedup

                # best_gl 同线变化
                same_line_bm = [
                    x for x in ou_snaps if abs(x[1] - float(best_gl)) < 0.001
                ]
                if len(same_line_bm) >= self.OU_ODDS_MIN_SAMPLES:
                    first = same_line_bm[0]
                    last = same_line_bm[-1]
                    if first[2] is not None and last[2] is not None:
                        over_moves.append(first[2] - last[2])
                    if first[3] is not None and last[3] is not None:
                        under_moves.append(first[3] - last[3])

                # 整线位移
                if len(ou_snaps) >= self.OU_ODDS_MIN_SAMPLES:
                    first = ou_snaps[0]
                    last = ou_snaps[-1]
                    if first[1] is not None and last[1] is not None:
                        gl_moves.append(last[1] - first[1])

            feats["over_odds_movement"] = self._safe_mean(over_moves)
            feats["under_odds_movement"] = self._safe_mean(under_moves)
            feats["goal_line_change"] = self._safe_mean(gl_moves)

        return feats
