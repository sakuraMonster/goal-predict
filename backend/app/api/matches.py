"""
赛事相关 API 路由
日期定义：竞彩"比赛日"为当日12:00至次日12:00
"""
import json
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import date, timedelta, datetime, timezone

BEIJING_TZ = timezone(timedelta(hours=8))


def _to_beijing_str(dt: datetime) -> str:
    """将 UTC naive datetime 转为北京时间字符串"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

from app.db.database import get_db
from app.db.models import Match, Team, League, OddsSnapshot, Prediction, TeamSeasonStats, HeadToHead

router = APIRouter(prefix="/api/matches", tags=["matches"])


def _date_range(day_str: str):
    """将 YYYY-MM-DD 转换为竞彩比赛日范围 [当日12:00, 次日12:00)"""
    d = datetime.strptime(day_str, "%Y-%m-%d")
    start = d.replace(hour=12, minute=0, second=0)
    end = start + timedelta(hours=24)
    return start, end


@router.get("")
async def list_matches(
    date: str = Query(None, description="日期 YYYY-MM-DD，不传默认今天"),
    league_id: int = Query(None),
    days: int = Query(1, description="查询天数（1=单日，3=未来3天），与date配合使用"),
    limit: int = Query(150, description="最大返回数量"),
    db: AsyncSession = Depends(get_db),
):
    """赛事列表（含预测摘要），支持多日范围查询"""
    from sqlalchemy.orm import joinedload

    query = select(Match).options(
        joinedload(Match.home_team),
        joinedload(Match.away_team),
        joinedload(Match.league),
    )
    # 不传日期时默认今天
    if date:
        filter_date = date
    else:
        filter_date = datetime.now().strftime("%Y-%m-%d")

    if days > 1:
        # 多日范围：[date 12:00, (date + days) 12:00)
        start, _ = _date_range(filter_date)
        end_dt = datetime.strptime(filter_date, "%Y-%m-%d") + timedelta(days=days)
        end = end_dt.replace(hour=12, minute=0, second=0)
        query = query.where(and_(Match.kickoff_time >= start, Match.kickoff_time < end))
    else:
        start, end = _date_range(filter_date)
        query = query.where(and_(Match.kickoff_time >= start, Match.kickoff_time < end))
    if league_id:
        query = query.where(Match.league_id == league_id)
    query = query.order_by(Match.match_num).limit(limit)
    result = await db.execute(query)
    matches = result.unique().scalars().all()

    data = []
    for m in matches:
        pred_result = await db.execute(
            select(Prediction).where(Prediction.match_id == m.id)
        )
        pred = pred_result.scalar_one_or_none()

        data.append({
            "id": m.id,
            "jc_match_id": m.jc_match_id,
            "match_num": m.match_num,
            "status": m.status,
            "league_name": m.league.name_zh if m.league else (m.venue or ""),
            "league_id": m.league_id,
            "kickoff_time": str(m.kickoff_time),
            "home_team": m.home_team.name_zh if m.home_team else (m.home_team_name or ""),
            "away_team": m.away_team.name_zh if m.away_team else (m.away_team_name or ""),
            "handicap_line": m.handicap_line,
            "home_prob": pred.home_prob if pred else 0.33,
            "draw_prob": pred.draw_prob if pred else 0.34,
            "away_prob": pred.away_prob if pred else 0.33,
            "expected_goals": pred.expected_goals if pred else 2.5,
            "snap_top2": pred.snap_top2 if pred else None,
            "expected_goals_c": pred.expected_goals_c if pred else None,
            "snap_top2_c": pred.snap_top2_c if pred else None,
            "goal_distribution": (pred.goal_distribution if pred and pred.goal_distribution else []),
            "score_top5_json": (pred.score_top5_json if pred and pred.score_top5_json else []),
            "reference_score": pred.score_top5_json[0]["score"] if pred and pred.score_top5_json else "?",
            "is_cold_match": pred.is_cold_match if pred else False,
            "cold_correction": pred.cold_correction if pred else None,
            "is_hot_match": (pred.confidence_level == "high" and not (pred.is_cold_match if pred else True)) if pred else False,
            "confidence_level": pred.confidence_level if pred else "low",
            "risk_warning": json.loads(pred.risk_warning) if pred and pred.risk_warning else None,
        })
    return {"data": data}


@router.get("/dates")
async def get_match_dates(
    range_days: int = Query(30, description="展示近N天，默认30天"),
    db: AsyncSession = Depends(get_db),
):
    """可查询的比赛日列表（近30天，倒序，12:00-次日12:00）"""
    today = date.today()
    dates = []

    # 近30天：从今天倒序到30天前
    for i in range(30):
        d = today - timedelta(days=i)
        day_str = d.isoformat()
        start, end = _date_range(day_str)
        result = await db.execute(
            select(func.count(Match.id)).where(
                and_(Match.kickoff_time >= start, Match.kickoff_time < end)
            )
        )
        count = result.scalar() or 0
        if count > 0:
            dates.append({"date": day_str, "count": count, "is_past": i > 0})

    return {"data": dates}


@router.get("/leagues")
async def get_leagues(
    date: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """联赛筛选列表（仅展示当日有赛事的联赛 + 场次数）"""
    if date:
        start, end = _date_range(date)

        # 1. 有 league_id 的比赛 → 查 League 表
        subq = select(Match.league_id).where(
            and_(Match.kickoff_time >= start, Match.kickoff_time < end, Match.league_id != None)
        ).distinct()
        result = await db.execute(select(League).where(League.id.in_(subq)))
        leagues = result.scalars().all()

        data = []
        seen_names = set()
        for l in leagues:
            cnt_result = await db.execute(
                select(func.count(Match.id)).where(
                    and_(Match.kickoff_time >= start, Match.kickoff_time < end, Match.league_id == l.id)
                )
            )
            count = cnt_result.scalar() or 0
            data.append({"id": l.id, "name": l.name_zh, "count": count})
            seen_names.add(l.name_zh)

        # 2. 无 league_id 的比赛 → 按 venue（原始联赛名）聚合
        raw_result = await db.execute(
            select(Match.venue, func.count(Match.id)).where(
                and_(
                    Match.kickoff_time >= start,
                    Match.kickoff_time < end,
                    Match.league_id == None,
                    Match.venue != "",
                )
            ).group_by(Match.venue)
        )
        for venue_name, count in raw_result.all():
            if venue_name and venue_name not in seen_names:
                data.append({"id": None, "name": venue_name, "count": count})

        return {"data": data}
    else:
        result = await db.execute(select(League).where(League.active == True))
        leagues = result.scalars().all()
        return {"data": [{"id": l.id, "name": l.name_zh} for l in leagues]}


@router.get("/{match_id}")
async def get_match_detail(match_id: int, db: AsyncSession = Depends(get_db)):
    """单场赛事完整详情"""
    from sqlalchemy.orm import joinedload

    result = await db.execute(
        select(Match)
        .options(joinedload(Match.home_team), joinedload(Match.away_team), joinedload(Match.league))
        .where(Match.id == match_id)
    )
    match = result.unique().scalar_one_or_none()
    if not match:
        return {"error": "not found"}, 404

    # 获取球队 logo：优先 DB 已存储的，否则尝试从 SM fixtures API 实时拉取
    home_logo = match.home_team.logo_url if match.home_team else ""
    away_logo = match.away_team.logo_url if match.away_team else ""

    if (not home_logo or not away_logo) and match.sportmonks_fixture_id:
        try:
            from app.collector.sportmonks.client import SportMonksClient
            sm = SportMonksClient()
            fx_data = await sm.get_fixture_by_id(match.sportmonks_fixture_id, includes="participants")
            participants = fx_data.get("participants", [])
            for p in participants:
                if not isinstance(p, dict):
                    continue
                pid = p.get("id")
                img = p.get("image_path", "")
                if not img:
                    continue
                # 更新对应球队的 logo_url
                if match.home_team and pid == match.home_team.sportmonks_id and not home_logo:
                    home_logo = img
                    match.home_team.logo_url = img
                if match.away_team and pid == match.away_team.sportmonks_id and not away_logo:
                    away_logo = img
                    match.away_team.logo_url = img
            await db.commit()
            await sm.close()
        except Exception:
            pass  # SM 不可用时静默降级

    return {"data": {
        "id": match.id,
        "jc_match_id": match.jc_match_id,
        "match_num": match.match_num,
        "sportmonks_fixture_id": match.sportmonks_fixture_id,
        "league_name": match.league.name_zh if match.league else (match.venue or ""),
        "kickoff_time": str(match.kickoff_time),
        "home_team": match.home_team.name_zh if match.home_team else (match.home_team_name or ""),
        "away_team": match.away_team.name_zh if match.away_team else (match.away_team_name or ""),
        "home_logo": home_logo,
        "away_logo": away_logo,
        "home_rank": "",
        "away_rank": "",
        "venue": match.venue,
        "handicap_line": match.handicap_line,
        "status": match.status,
    }}


@router.get("/{match_id}/odds-history")
async def get_odds_history(match_id: int, db: AsyncSession = Depends(get_db)):
    # 同时获取赛事基本信息（让球线）
    match_result = await db.execute(select(Match).where(Match.id == match_id))
    match = match_result.scalar_one_or_none()

    result = await db.execute(
        select(OddsSnapshot).where(OddsSnapshot.match_id == match_id).order_by(OddsSnapshot.snapshot_time.asc())
    )
    odds = result.scalars().all()

    # 按博彩公司分组
    bookmaker_data: dict[str, list] = {}
    opening: dict[str, dict] = {}  # 初盘（is_opening=True 的快照）

    for o in odds:
        bm = o.bookmaker or "unknown"
        point = {
            "time": _to_beijing_str(o.snapshot_time),
            "home_win": o.home_win,
            "draw": o.draw,
            "away_win": o.away_win,
            "handicap_line": o.handicap_line,
            "handicap_home": o.handicap_home,
            "handicap_away": o.handicap_away,
        }
        if bm not in bookmaker_data:
            bookmaker_data[bm] = []
        bookmaker_data[bm].append(point)

        # 初盘：取 is_opening=True 的记录
        if getattr(o, "is_opening", False) and bm not in opening:
            opening[bm] = point

    # 兜底：如果没有 is_opening 标记，取最早一条
    if not opening:
        for o in odds:
            bm = o.bookmaker or "unknown"
            if bm not in opening:
                opening[bm] = {
                    "time": _to_beijing_str(o.snapshot_time),
                    "home_win": o.home_win,
                    "draw": o.draw,
                    "away_win": o.away_win,
                    "handicap_line": o.handicap_line,
                    "handicap_home": o.handicap_home,
                    "handicap_away": o.handicap_away,
                }

    return {
        "data": bookmaker_data,
        "match_handicap_line": match.handicap_line if match else None,
        "opening": opening,
    }


@router.get("/{match_id}/h2h")
async def get_h2h(match_id: int, db: AsyncSession = Depends(get_db)):
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

    # 获取球队名称
    team_ids = set()
    for h in h2h:
        team_ids.add(h.home_team_id)
        team_ids.add(h.away_team_id)
    team_names = {}
    if team_ids:
        teams_result = await db.execute(select(Team.id, Team.name_zh).where(Team.id.in_(list(team_ids))))
        for row in teams_result:
            team_names[row[0]] = row[1] or ""

    return {"data": [{
        "date": str(h.match_date),
        "home_team": team_names.get(h.home_team_id, ""),
        "away_team": team_names.get(h.away_team_id, ""),
        "home_score": h.home_score,
        "away_score": h.away_score,
    } for h in h2h]}
