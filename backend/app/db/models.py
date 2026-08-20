from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, Enum, JSON, Date, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

_JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")

class League(Base):
    __tablename__ = "leagues"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sportmonks_id = Column(Integer, unique=True, index=True, nullable=True)
    name_zh = Column(String(100), nullable=False)
    name_en = Column(String(100), nullable=False)
    country = Column(String(100))
    season = Column(String(20))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class LeagueAlias(Base):
    __tablename__ = "league_aliases"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    alias_name = Column(String(200), nullable=False)
    source = Column(String(50))
    is_primary = Column(Boolean, default=False)

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sportmonks_id = Column(Integer, unique=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    name_zh = Column(String(100))
    name_en = Column(String(100), nullable=False)
    short_zh = Column(String(50))
    short_en = Column(String(50))
    logo_url = Column(String(500))
    style_tag = Column(String(20))
    needs_review = Column(Boolean, default=False, comment="需要人工确认 SportMonks 映射")
    review_reason = Column(String(200), comment="待确认原因")
    created_at = Column(DateTime, default=datetime.utcnow)

class TeamAlias(Base):
    __tablename__ = "team_aliases"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    alias_name = Column(String(200), nullable=False)
    source = Column(String(50))
    is_primary = Column(Boolean, default=False)
    league_name_zh = Column(String(100))  # 联赛中文名称，辅助人工匹配

class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True, autoincrement=True)
    jc_match_id = Column(String(50), unique=True, index=True)
    match_num = Column(String(50))  # 竞彩网场次编号（如"周日104"）
    sportmonks_fixture_id = Column(Integer, index=True)  # SportMonks fixture ID
    league_id = Column(Integer, ForeignKey("leagues.id"))
    home_team_id = Column(Integer, ForeignKey("teams.id"))
    away_team_id = Column(Integer, ForeignKey("teams.id"))
    home_team_name = Column(String(100))  # 原始队名（未匹配时使用）
    away_team_name = Column(String(100))  # 原始队名（未匹配时使用）
    kickoff_time = Column(DateTime, nullable=False)
    status = Column(String(20), default="scheduled")
    handicap_line = Column(Float)
    is_swapped = Column(Boolean, default=False)  # SportMonks 主客与竞彩网是否相反
    venue = Column(String(200))
    home_score = Column(Integer)
    away_score = Column(Integer)
    half_home_score = Column(Integer)
    half_away_score = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    home_team = relationship("Team", foreign_keys=[home_team_id], lazy="joined")
    away_team = relationship("Team", foreign_keys=[away_team_id], lazy="joined")
    league = relationship("League", foreign_keys=[league_id], lazy="joined")

class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    snapshot_time = Column(DateTime, nullable=False)
    bookmaker = Column(String(100))
    home_win = Column(Float)
    draw = Column(Float)
    away_win = Column(Float)
    handicap_home = Column(Float)
    handicap_line = Column(Float)
    handicap_away = Column(Float)
    over_odds = Column(Float)
    goal_line = Column(Float)
    under_odds = Column(Float)
    is_opening = Column(Boolean, default=False)  # 是否为初盘赔率

class JczqPlayOddsSnapshot(Base):
    __tablename__ = "jczq_play_odds_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    snapshot_time = Column(DateTime, nullable=False)
    source = Column(String(50), nullable=False)
    had_home = Column(Float)
    had_draw = Column(Float)
    had_away = Column(Float)
    hhad_line = Column(Float)
    hhad_home = Column(Float)
    hhad_draw = Column(Float)
    hhad_away = Column(Float)
    ttg_odds_json = Column(_JSON_VARIANT)
    crs_odds_json = Column(_JSON_VARIANT)

class TeamSeasonStats(Base):
    __tablename__ = "team_season_stats"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    season = Column(String(20))
    league_id = Column(Integer, ForeignKey("leagues.id"))
    played = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    draws = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    goals_for = Column(Integer, default=0)
    goals_against = Column(Integer, default=0)
    home_wins = Column(Integer, default=0)
    home_draws = Column(Integer, default=0)
    home_losses = Column(Integer, default=0)
    away_wins = Column(Integer, default=0)
    away_draws = Column(Integer, default=0)
    away_losses = Column(Integer, default=0)
    clean_sheets = Column(Integer, default=0)
    failed_to_score = Column(Integer, default=0)
    avg_possession = Column(Float)
    xG = Column(Float)
    xGA = Column(Float)
    xPTS = Column(Float)
    form = Column(String(50))
    recent_matches = Column(JSON)  # 最近10场比赛 [{opponent, score, result, date}]
    # V4: 联赛排名
    league_position = Column(Integer)  # 当前联赛排名（1=榜首）
    league_points = Column(Integer)     # 当前积分
    league_goal_diff = Column(Integer)  # 净胜球

class LeagueSeasonBaseline(Base):
    """联赛历史赛季基线 —— 新赛季前期实时数据不足时的降级数据源

    由 tools/precompute_league_baselines.py 基于 team_season_stats 完整赛季记录聚合生成。
    字段与 features_base._get_league_baseline 返回结构一一对应，可直接替换使用。
    """
    __tablename__ = "league_season_baselines"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False, index=True)
    season = Column(String(20), nullable=False, comment="对应 team_season_stats.season，如 '2025'")
    league_count = Column(Integer, default=0, comment="参与聚合的球队数")
    league_avg_home_goals = Column(Float, comment="联赛场均主队进球（无主客场分列时的近似值）")
    league_avg_away_goals = Column(Float, comment="联赛场均客队进球（无主客场分列时的近似值）")
    league_home_win_rate = Column(Float, comment="联赛主胜率 = sum(home_wins)/sum(played)")
    league_draw_rate = Column(Float, comment="联赛平局率 = sum(draws)/sum(played)")
    league_avg_total_goals = Column(Float, comment="联赛场均总进球 = sum(goals_for)/sum(played)")
    source = Column(String(100), comment="数据来源说明")
    created_at = Column(DateTime, default=datetime.utcnow)

class HeadToHead(Base):
    __tablename__ = "head_to_head"
    id = Column(Integer, primary_key=True)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    match_date = Column(DateTime, nullable=False)
    competition = Column(String(200))
    home_score = Column(Integer)
    away_score = Column(Integer)
    is_neutral_venue = Column(Boolean, default=False)
    sportmonks_fixture_id = Column(Integer, index=True)  # SportMonks fixture ID，用于拉取统计
    home_stats = Column(JSON)   # 主队比赛统计 {xG, possession, shots, attacks, ...}
    away_stats = Column(JSON)   # 客队比赛统计

class Injury(Base):
    __tablename__ = "injuries"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    player_name = Column(String(200))
    type = Column(String(50))
    reason = Column(String(500))
    start_date = Column(DateTime)
    expected_return = Column(DateTime)
    status = Column(String(20), default="out")

class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, unique=True, index=True)
    model_version = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # ── 联赛（冗余，方便按联赛统计） ──
    league_id = Column(Integer, ForeignKey("leagues.id"), index=True)
    
    # ── 比赛时间（冗余，方便按时间筛选） ──
    kickoff_time = Column(DateTime, index=True, comment="比赛开球时间")
    
    # ── 关联 ──
    match = relationship("Match", foreign_keys=[match_id], lazy="joined")
    league = relationship("League", foreign_keys=[league_id], lazy="joined")
    
    # ── 预测概率 ──
    home_prob = Column(Float)
    draw_prob = Column(Float)
    away_prob = Column(Float)
    handicap_home_prob = Column(Float)
    handicap_draw_prob = Column(Float)
    handicap_away_prob = Column(Float)
    expected_goals = Column(Float)
    over_2_5_prob = Column(Float)
    goal_distribution = Column(JSON)
    snap_top2 = Column(JSON, comment="SNAP 算法距离预期进球最近的2个整数，如 [3,4]")
    # Model C & D 并行: 预测进球数 + SNAP
    expected_goals_c = Column(Float, comment="Model C 预期进球 (市场基线)")
    expected_goals_d = Column(Float, comment="Model D 预期进球 (Dixon-Coles)")
    snap_top2_c = Column(JSON, comment="Model C SNAP")
    snap_top2_d = Column(JSON, comment="Model D SNAP")
    score_top5_json = Column(JSON)
    summary_text = Column(Text)
    key_factors = Column(Text)
    risk_warning = Column(Text)
    confidence_level = Column(String(20))
    is_cold_match = Column(Boolean, default=False)
    cold_correction = Column(JSON, comment="冷门概率修正详情：包含原始概率、市场概率、融合比例")
    
    # ── 实际结果（比赛结束后回写） ──
    actual_home_score = Column(Integer, comment="实际主队进球")
    actual_away_score = Column(Integer, comment="实际客队进球")
    actual_total_goals = Column(Integer, comment="实际总进球")
    actual_score = Column(String(10), comment="实际比分 e.g. 2:1")
    
    # 四种玩法结果: 0=未结算, 1=命中, -1=未命中
    result_spf = Column(Integer, default=0, comment="胜平负结果 1=中/-1=不中/0=未结算")
    result_hcp = Column(Integer, default=0, comment="让球胜平负结果 1=中/-1=不中/0=未结算")
    result_goals = Column(Integer, default=0, comment="进球数结果 1=中/-1=不中/0=未结算")
    result_score = Column(Integer, default=0, comment="比分结果 1=中/-1=不中/0=未结算")

class MarketFlowPrediction(Base):
    __tablename__ = "market_flow_predictions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, unique=True, index=True)
    odds_snapshot_id = Column(Integer, ForeignKey("jczq_play_odds_snapshots.id"), nullable=False)
    model_version = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    home_style_tag = Column(String(20), nullable=False, default="均衡")
    away_style_tag = Column(String(20), nullable=False, default="均衡")
    best_total_goals = Column(Integer)
    second_total_goals = Column(Integer)
    best_score = Column(String(20))
    second_score = Column(String(20))
    trace_json = Column(_JSON_VARIANT)

    match = relationship("Match", foreign_keys=[match_id], lazy="joined")
    odds_snapshot = relationship("JczqPlayOddsSnapshot", foreign_keys=[odds_snapshot_id], lazy="joined")

class TaskLog(Base):
    __tablename__ = "task_logs"
    id = Column(Integer, primary_key=True)
    task_type = Column(String(50), nullable=False)
    status = Column(String(20))
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    duration_ms = Column(Integer)
    message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class GoalPickRecord(Base):
    """进球数优选推荐快照（每日 top_n 场），用于历史命中率统计"""
    __tablename__ = "goal_pick_records"
    __table_args__ = (UniqueConstraint("pick_date", "rank", name="uq_goal_pick_date_rank"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    pick_date = Column(Date, nullable=False, index=True, comment="推荐对应的比赛日（竞彩 12:00 口径）")
    rank = Column(Integer, nullable=False, comment="推荐排名 1~top_n")
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    match_num = Column(String(50))
    league_name = Column(String(100))
    home_team = Column(String(100))
    away_team = Column(String(100))
    kickoff_time = Column(DateTime)
    expected_goals_c = Column(Float)
    snap_top2_c = Column(JSON, comment="推荐时的 Model C SNAP 快照，如 [2,3]")
    score = Column(Float, comment="把握度评分")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ColdPickRecord(Base):
    """冷门优选推荐快照（每日 top_n 场），用于历史命中率统计

    规则：同联赛场次中，市场热门方向（收盘隐含概率 argmax）与排名优势方向相反
    → 市场高估 → 冷门风险高。rank_gap 越大，信号越强。
    """
    __tablename__ = "cold_pick_records"
    __table_args__ = (UniqueConstraint("pick_date", "rank", name="uq_cold_pick_date_rank"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    pick_date = Column(Date, nullable=False, index=True, comment="推荐对应的比赛日（竞彩 12:00 口径）")
    rank = Column(Integer, nullable=False, comment="推荐排名 1~top_n")
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    match_num = Column(String(50))
    league_name = Column(String(100))
    home_team = Column(String(100))
    away_team = Column(String(100))
    kickoff_time = Column(DateTime)
    fav_dir = Column(Integer, comment="市场热门方向 0=主胜 1=平 2=客胜")
    fav_prob = Column(Float, comment="市场热门方向隐含概率")
    rank_gap = Column(Integer, comment="同联赛排名差距 |home_pos-away_pos|")
    odds_delta_max = Column(Float, comment="盘口资金异动：开盘→收盘三方向隐含概率最大变动（待验证的叠加条件特征）")
    odds_step_max = Column(Float, comment="盘口资金异动：相邻快照时刻最大单步跳变（待验证的叠加条件特征）")
    score = Column(Float, comment="把握度评分（rank_gap 为主）")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
