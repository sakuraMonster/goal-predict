"""
赛事相关 API 路由
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date, timedelta
from app.db.database import get_db
from app.db.models import Match, Team, League, OddsSnapshot, Prediction, TeamSeasonStats, HeadToHead

router = APIRouter(prefix="/api/matches", tags=["matches"])


@router.get("")
async def list_matches(
    date: str = Query(None, description="日期 YYYY-MM-DD"),
    league_id: int = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """赛事列表（含预测摘要）"""
    query = select(Match).join(Team, Match.home_team_id == Team.id).join(League, Match.league_id == League.id)
    if date:
        query = query.where(func.date(Match.kickoff_time) == date)
    if league_id:
        query = query.where(Match.league_id == league_id)
    query = query.order_by(Match.kickoff_time)
    result = await db.execute(query)
    matches = result.scalars().all()
    return {"data": [{"id": m.id, "jc_match_id": m.jc_match_id, "kickoff_time": str(m.kickoff_time),
                       "venue": m.venue, "handicap_line": m.handicap_line, "status": m.status} for m in matches]}


@router.get("/dates")
async def get_match_dates(db: AsyncSession = Depends(get_db)):
    """未来3日日期+每日场次数"""
    today = date.today()
    dates = []
    for i in range(4):
        d = today + timedelta(days=i)
        result = await db.execute(
            select(func.count(Match.id)).where(func.date(Match.kickoff_time) == d.isoformat())
        )
        count = result.scalar() or 0
        dates.append({"date": d.isoformat(), "count": count})
    return {"data": dates}


@router.get("/leagues")
async def get_leagues(date: str = Query(None), db: AsyncSession = Depends(get_db)):
    """联赛筛选列表+场次数"""
    result = await db.execute(select(League).where(League.active == True))
    leagues = result.scalars().all()
    return {"data": [{"id": l.id, "name": l.name_zh, "country": l.country} for l in leagues]}


@router.get("/{match_id}")
async def get_match_detail(match_id: int, db: AsyncSession = Depends(get_db)):
    """单场赛事完整详情"""
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match:
        return {"error": "not found"}, 404
    return {"data": {"id": match.id, "jc_match_id": match.jc_match_id, "kickoff_time": str(match.kickoff_time),
                      "venue": match.venue, "handicap_line": match.handicap_line, "status": match.status,
                      "home_score": match.home_score, "away_score": match.away_score}}


@router.get("/{match_id}/odds-history")
async def get_odds_history(match_id: int, db: AsyncSession = Depends(get_db)):
    """赔率变动时间序列"""
    result = await db.execute(
        select(OddsSnapshot).where(OddsSnapshot.match_id == match_id).order_by(OddsSnapshot.snapshot_time.desc())
    )
    odds = result.scalars().all()
    return {"data": [{"time": str(o.snapshot_time), "home_win": o.home_win, "draw": o.draw,
                       "away_win": o.away_win, "handicap_line": o.handicap_line} for o in odds]}


@router.get("/{match_id}/h2h")
async def get_h2h(match_id: int, db: AsyncSession = Depends(get_db)):
    """两队近期交锋"""
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match:
        return {"data": []}
    h2h_result = await db.execute(
        select(HeadToHead).where(
            ((HeadToHead.home_team_id == match.home_team_id) & (HeadToHead.away_team_id == match.away_team_id)) |
            ((HeadToHead.home_team_id == match.away_team_id) & (HeadToHead.away_team_id == match.home_team_id))
        ).order_by(HeadToHead.match_date.desc()).limit(6)
    )
    h2h = h2h_result.scalars().all()
    return {"data": [{"date": str(h.match_date), "home_score": h.home_score, "away_score": h.away_score} for h in h2h]}
