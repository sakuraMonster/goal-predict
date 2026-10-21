"""价值分析 API —— 用「锐利盘基准 + 去水 + EV」对每日比赛做价值筛选与风险提示。

数据源（均已存在于库，无需新增采集）：
- 竞彩盘口：jczq_play_odds_snapshots（HAD/HHAD/TTG/CRS/HAFU）
- 锐利盘基准：odds_snapshots（bet365 1X2 + SportMonks O/U 多庄家）

价值口径：锐利盘去水概率 = 「真实概率」，EV = 真实概率 × 竞彩赔率 − 1。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.market_flow import _sm_ou_rows
from app.db.database import get_db
from app.db.models import JczqPlayOddsSnapshot, League, Match, OddsSnapshot, Team
from app.predictor.models.ou_market import ou_market_from_rows
from app.predictor.models.value_analysis import analyze_match

router = APIRouter(prefix="/api/value-analysis", tags=["value_analysis"])

_BEIJING = timezone(timedelta(hours=8))


def _beijing_today() -> str:
    return datetime.now(_BEIJING).strftime("%Y-%m-%d")


async def _jczq_map(db: AsyncSession, match_ids: list[int], desc: bool = True) -> dict[int, JczqPlayOddsSnapshot]:
    """批量取每场竞彩快照：desc=True 取最新、False 取最早。"""
    if not match_ids:
        return {}
    stmt = (
        select(JczqPlayOddsSnapshot)
        .where(JczqPlayOddsSnapshot.match_id.in_(match_ids))
        .distinct(JczqPlayOddsSnapshot.match_id)
        .order_by(
            JczqPlayOddsSnapshot.match_id.asc(),
            JczqPlayOddsSnapshot.snapshot_time.desc() if desc else JczqPlayOddsSnapshot.snapshot_time.asc(),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {int(s.match_id): s for s in rows}


async def _sharp_1x2_map(db: AsyncSession, match_ids: list[int]) -> dict[int, dict]:
    """批量取每场锐利盘最新 1X2（3-way 基准）：Pinnacle 优先，缺失降级 bet365。

    Buchdahl 口径：Pinnacle 是最锐的 3-way 基准（接纳锐利玩家而非封杀），bet365 作为兜底。
    """
    if not match_ids:
        return {}

    async def _fetch(bookmaker: str) -> dict[int, dict]:
        stmt = (
            select(OddsSnapshot.match_id, OddsSnapshot.home_win, OddsSnapshot.draw, OddsSnapshot.away_win)
            .where(
                OddsSnapshot.match_id.in_(match_ids),
                OddsSnapshot.bookmaker == bookmaker,
                OddsSnapshot.home_win.is_not(None),
                OddsSnapshot.draw.is_not(None),
                OddsSnapshot.away_win.is_not(None),
            )
            .distinct(OddsSnapshot.match_id)
            .order_by(OddsSnapshot.match_id.asc(), OddsSnapshot.snapshot_time.desc())
        )
        rows = (await db.execute(stmt)).all()
        return {int(mid): {"home": float(h), "draw": float(d), "away": float(a)} for mid, h, d, a in rows}

    pinnacle = await _fetch("Pinnacle")
    bet365 = await _fetch("bet365")
    # Pinnacle 优先，缺失时用 bet365 兜底
    merged = dict(bet365)
    merged.update(pinnacle)
    return merged


def _jczq_dict(snap: JczqPlayOddsSnapshot) -> dict:
    return {
        "had": {"home": snap.had_home, "draw": snap.had_draw, "away": snap.had_away},
        "hhad": {"line": snap.hhad_line, "home": snap.hhad_home, "draw": snap.hhad_draw, "away": snap.hhad_away},
        "ttg": snap.ttg_odds_json,
        "crs": snap.crs_odds_json,
        "hafu": snap.hafu_odds_json,
    }


@router.get("")
async def value_analysis(
    date: str | None = Query(None, description="比赛日 YYYY-MM-DD（北京时间），缺省=今天"),
    db: AsyncSession = Depends(get_db),
):
    date = date or _beijing_today()
    try:
        day = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"error": "date 需为 YYYY-MM-DD 格式"}

    # 比赛周期：当日 12:00 ~ 次日 12:00（竞彩比赛日口径）
    start = day + timedelta(hours=12)
    end = start + timedelta(days=1)
    HomeTeam = aliased(Team, name="home_team")
    AwayTeam = aliased(Team, name="away_team")

    m_res = await db.execute(
        select(Match, HomeTeam.name_zh, AwayTeam.name_zh, League.name_zh)
        .outerjoin(HomeTeam, HomeTeam.id == Match.home_team_id)
        .outerjoin(AwayTeam, AwayTeam.id == Match.away_team_id)
        .outerjoin(League, League.id == Match.league_id)
        .where(Match.kickoff_time >= start, Match.kickoff_time < end)
        .order_by(Match.kickoff_time.asc(), Match.id.asc())
    )
    mrows = m_res.all()
    mids = [int(m.id) for m, *_ in mrows]

    latest_jczq = await _jczq_map(db, mids, desc=True)
    earliest_jczq = await _jczq_map(db, mids, desc=False)
    sharp_1x2_map = await _sharp_1x2_map(db, mids)
    ou_map = await _sm_ou_rows(db, mids)

    items = []
    for m, hname, aname, lname in mrows:
        mid = int(m.id)
        snap = latest_jczq.get(mid)
        jczq = _jczq_dict(snap) if snap else None
        jczq_early = _jczq_dict(earliest_jczq[mid]) if earliest_jczq.get(mid) else None

        s1x2 = sharp_1x2_map.get(mid)
        ou = ou_market_from_rows(ou_map.get(mid) or [])
        sharp_ou_p_big = ou["p_big"] if ou else None
        sharp_ou_n_books = ou["n_books"] if ou else None

        analysis = (
            analyze_match(
                jczq=jczq,
                sharp_1x2=s1x2,
                sharp_ou_p_big=sharp_ou_p_big,
                sharp_ou_n_books=sharp_ou_n_books,
                jczq_early=jczq_early,
            )
            if jczq
            else None
        )

        items.append({
            "match_id": mid,
            "match_num": m.match_num,
            "league": lname or getattr(m, "league_name", None),
            "home": hname or m.home_team_name,
            "away": aname or m.away_team_name,
            "kickoff": m.kickoff_time.isoformat() if m.kickoff_time else None,
            "status": m.status,
            "analysis": analysis,
        })

    value_match_count = sum(
        1 for it in items if it["analysis"] and it["analysis"]["risk_summary"]["value_markets"]
    )
    return {
        "date": date,
        "total": len(items),
        "value_match_count": value_match_count,
        "matches": items,
    }
