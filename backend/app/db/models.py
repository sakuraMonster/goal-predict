from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, Enum, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

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
