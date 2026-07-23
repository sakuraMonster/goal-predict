from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, Enum, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class League(Base):
    __tablename__ = "leagues"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sportmonks_id = Column(Integer, unique=True, index=True)
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
    created_at = Column(DateTime, default=datetime.utcnow)

class TeamAlias(Base):
    __tablename__ = "team_aliases"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    alias_name = Column(String(200), nullable=False)
    source = Column(String(50))
    is_primary = Column(Boolean, default=False)

class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True, autoincrement=True)
    jc_match_id = Column(String(50), unique=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    home_team_id = Column(Integer, ForeignKey("teams.id"))
    away_team_id = Column(Integer, ForeignKey("teams.id"))
    kickoff_time = Column(DateTime, nullable=False)
    status = Column(String(20), default="scheduled")
    handicap_line = Column(Float)
    venue = Column(String(200))
    home_score = Column(Integer)
    away_score = Column(Integer)
    half_home_score = Column(Integer)
    half_away_score = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    form = Column(String(20))

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
    home_prob = Column(Float)
    draw_prob = Column(Float)
    away_prob = Column(Float)
    handicap_home_prob = Column(Float)
    handicap_draw_prob = Column(Float)
    handicap_away_prob = Column(Float)
    expected_goals = Column(Float)
    over_2_5_prob = Column(Float)
    goal_distribution = Column(JSON)
    score_top5_json = Column(JSON)
    summary_text = Column(Text)
    key_factors = Column(Text)
    risk_warning = Column(Text)
    confidence_level = Column(String(20))
    is_cold_match = Column(Boolean, default=False)

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
