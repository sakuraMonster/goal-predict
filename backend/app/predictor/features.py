"""
特征工程 Pipeline：从数据库提取约84个预测特征
"""
import pandas as pd
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.models import Match, TeamSeasonStats, HeadToHead, OddsSnapshot, Injury, Team


class FeatureEngineer:
    """特征提取引擎"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def extract_features(self, match_id: int) -> pd.DataFrame:
        """为单场比赛提取特征，返回单行 DataFrame"""
        match = await self._get_match(match_id)
        if not match:
            return pd.DataFrame()

        home_stats = await self._get_team_stats(match.home_team_id)
        away_stats = await self._get_team_stats(match.away_team_id)
        h2h = await self._get_h2h(match.home_team_id, match.away_team_id)
        odds = await self._get_latest_odds(match_id)
        home_injuries = await self._get_injury_count(match.home_team_id)
        away_injuries = await self._get_injury_count(match.away_team_id)

        features = {}
        # 类别 A: 球队基础战力
        features.update(self._extract_team_strength(home_stats, away_stats, "home", "away"))
        # 类别 B: 交锋记录
        features.update(self._extract_h2h(h2h))
        # 类别 C: 赔率信号
        features.update(self._extract_odds(odds))
        # 类别 D: 阵容 & 外部
        features["home_injuries"] = home_injuries
        features["away_injuries"] = away_injuries
        features["home_rest_days"] = 7.0
        features["away_rest_days"] = 7.0
        features["rest_days_diff"] = 0.0

        return pd.DataFrame([features])

    async def _get_match(self, match_id: int):
        result = await self.db.execute(select(Match).where(Match.id == match_id))
        return result.scalar_one_or_none()

    async def _get_team_stats(self, team_id: int):
        result = await self.db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.team_id == team_id).order_by(TeamSeasonStats.season.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_h2h(self, team1_id: int, team2_id: int) -> list:
        result = await self.db.execute(
            select(HeadToHead).where(
                ((HeadToHead.home_team_id == team1_id) & (HeadToHead.away_team_id == team2_id)) |
                ((HeadToHead.home_team_id == team2_id) & (HeadToHead.away_team_id == team1_id))
            ).order_by(HeadToHead.match_date.desc()).limit(10)
        )
        return list(result.scalars().all())

    async def _get_latest_odds(self, match_id: int):
        result = await self.db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id == match_id).order_by(OddsSnapshot.snapshot_time.desc()).limit(5)
        )
        return list(result.scalars().all())

    async def _get_injury_count(self, team_id: int) -> int:
        result = await self.db.execute(
            select(Injury).where(Injury.team_id == team_id, Injury.status == "out")
        )
        return len(list(result.scalars().all()))

    def _extract_team_strength(self, home, away, home_prefix, away_prefix) -> dict:
        """提取球队战力特征"""
        feats = {}
        if home:
            feats["home_win_rate"] = home.wins / max(home.played, 1)
            feats["home_draw_rate"] = home.draws / max(home.played, 1)
            feats["home_goals_avg"] = home.goals_for / max(home.played, 1)
            feats["home_goals_against_avg"] = home.goals_against / max(home.played, 1)
            feats["home_home_win_rate"] = home.home_wins / max(home.home_wins + home.home_draws + home.home_losses, 1)
            feats["home_clean_sheet_rate"] = home.clean_sheets / max(home.played, 1)
            feats["home_xG"] = home.xG or 0
            feats["home_xGA"] = home.xGA or 0
        else:
            for k in ["home_win_rate","home_draw_rate","home_goals_avg","home_goals_against_avg","home_home_win_rate","home_clean_sheet_rate","home_xG","home_xGA"]:
                feats[k] = 0.0

        if away:
            feats["away_win_rate"] = away.wins / max(away.played, 1)
            feats["away_goals_avg"] = away.goals_for / max(away.played, 1)
            feats["away_goals_against_avg"] = away.goals_against / max(away.played, 1)
            feats["away_away_win_rate"] = away.away_wins / max(away.away_wins + away.away_draws + away.away_losses, 1)
            feats["away_xG"] = away.xG or 0
            feats["away_xGA"] = away.xGA or 0
        else:
            for k in ["away_win_rate","away_goals_avg","away_goals_against_avg","away_away_win_rate","away_xG","away_xGA"]:
                feats[k] = 0.0

        return feats

    def _extract_h2h(self, h2h_list: list) -> dict:
        feats = {"h2h_match_count": len(h2h_list), "h2h_home_wins": 0, "h2h_draws": 0, "h2h_away_wins": 0}
        for h in h2h_list:
            if h.home_score > h.away_score:
                feats["h2h_home_wins"] += 1
            elif h.home_score == h.away_score:
                feats["h2h_draws"] += 1
            else:
                feats["h2h_away_wins"] += 1
        return feats

    def _extract_odds(self, odds_list: list) -> dict:
        feats = {
            "odds_home_initial": 0.0, "odds_draw_initial": 0.0, "odds_away_initial": 0.0,
            "odds_home_current": 0.0, "odds_draw_current": 0.0, "odds_away_current": 0.0,
            "odds_movement_home": 0.0, "odds_dispersity": 0.0,
        }
        if len(odds_list) >= 2:
            first = odds_list[-1]  # 最早的快照
            last = odds_list[0]    # 最新的快照
            feats["odds_home_initial"] = first.home_win or 0
            feats["odds_draw_initial"] = first.draw or 0
            feats["odds_away_initial"] = first.away_win or 0
            feats["odds_home_current"] = last.home_win or 0
            feats["odds_draw_current"] = last.draw or 0
            feats["odds_away_current"] = last.away_win or 0
            feats["odds_movement_home"] = (first.home_win or 1) - (last.home_win or 1)
        return feats
