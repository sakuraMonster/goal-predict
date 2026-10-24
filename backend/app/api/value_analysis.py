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


def _actual_outcome(home_score: int, away_score: int) -> str:
    if home_score > away_score:
        return "home"
    if home_score == away_score:
        return "draw"
    return "away"


def _rate(n: int, d: int) -> float | None:
    """命中率：无分母返回 None（避免除零）。"""
    return round(n / d, 4) if d else None


@router.get("/history")
async def value_analysis_history(
    days: int = Query(90, description="回溯天数（默认近 90 天，1~365）"),
    db: AsyncSession = Depends(get_db),
):
    """价值分析历史命中统计：近 N 天每日「有价值」1X2 方向命中 + 每日预测/命中明细。

    口径与 /api/value-analysis 完全一致（每场取最新快照），仅对已结算场次（有比分）统计。
    命中定义：1X2 = best_side（最大 EV 方向）== 实际胜平负；大小球 = cheap_side 方向 == 实际方向。
    """
    days = max(1, min(days, 365))
    today = datetime.now(_BEIJING).date()
    start_day = today - timedelta(days=days - 1)

    # 比赛周期：matchday = (kickoff - 12h) 的北京时间日期
    start_ts = datetime.combine(start_day, datetime.min.time()) + timedelta(hours=12)
    end_ts = datetime.combine(today, datetime.min.time()) + timedelta(days=1, hours=12)

    HomeTeam = aliased(Team, name="home_team")
    AwayTeam = aliased(Team, name="away_team")

    m_res = await db.execute(
        select(Match, HomeTeam.name_zh, AwayTeam.name_zh, League.name_zh)
        .outerjoin(HomeTeam, HomeTeam.id == Match.home_team_id)
        .outerjoin(AwayTeam, AwayTeam.id == Match.away_team_id)
        .outerjoin(League, League.id == Match.league_id)
        .where(
            Match.kickoff_time >= start_ts,
            Match.kickoff_time < end_ts,
            Match.home_score.is_not(None),
            Match.away_score.is_not(None),
        )
        .order_by(Match.kickoff_time.asc(), Match.id.asc())
    )
    mrows = m_res.all()
    mids = [int(m.id) for m, *_ in mrows]

    latest_jczq = await _jczq_map(db, mids, desc=True)
    sharp_1x2_map = await _sharp_1x2_map(db, mids)
    ou_map = await _sm_ou_rows(db, mids)

    records = []
    for m, hname, aname, lname in mrows:
        mid = int(m.id)
        snap = latest_jczq.get(mid)
        if not snap:
            continue
        jczq = _jczq_dict(snap)
        s1x2 = sharp_1x2_map.get(mid)
        ou = ou_market_from_rows(ou_map.get(mid) or [])
        sharp_ou_p_big = ou["p_big"] if ou else None
        sharp_ou_n_books = ou["n_books"] if ou else None

        analysis = analyze_match(
            jczq=jczq,
            sharp_1x2=s1x2,
            sharp_ou_p_big=sharp_ou_p_big,
            sharp_ou_n_books=sharp_ou_n_books,
        )

        hs, aw = m.home_score, m.away_score
        actual = _actual_outcome(hs, aw)
        actual_over = (hs + aw) > 2.5

        v1 = analysis["markets"].get("1x2", {}).get("value", {})
        best_side = v1.get("best_side")
        verdict = v1.get("verdict")
        best_ev = v1.get("best_ev")
        outcomes = v1.get("outcomes") or {}
        best_fp = outcomes.get(best_side, {}).get("fair_prob") if best_side else None
        hit = (best_side == actual) if best_side else None

        ou_v = analysis["markets"].get("ou", {}).get("value", {})
        ou_cheap = ou_v.get("cheap_side")
        ou_hit = None
        if ou_cheap == "小球":
            ou_hit = not actual_over
        elif ou_cheap == "大球":
            ou_hit = actual_over

        matchday = (m.kickoff_time - timedelta(hours=12)).date().isoformat()

        records.append({
            "matchday": matchday,
            "match_num": m.match_num,
            "league": lname or getattr(m, "league_name", None),
            "home": hname or m.home_team_name,
            "away": aname or m.away_team_name,
            "kickoff": m.kickoff_time.strftime("%H:%M") if m.kickoff_time else None,
            "actual": actual,
            "verdict": verdict,
            "best_side": best_side,
            "best_ev": round(best_ev, 4) if best_ev is not None else None,
            "best_fp": round(best_fp, 4) if best_fp is not None else None,
            "hit": hit,
            "ou_cheap": ou_cheap,
            "ou_hit": ou_hit,
        })

    # 按比赛日聚合（新→旧）
    daily_map: dict[str, dict] = {}
    for r in records:
        d = daily_map.setdefault(r["matchday"], {
            "matchday": r["matchday"],
            "matches": [],
            "total": 0,
            "all_hit": 0,
            "value_n": 0,
            "value_hit": 0,
            "value_fp_sum": 0.0,
        })
        d["matches"].append(r)
        if r["best_side"] is not None:
            d["total"] += 1
            if r["hit"]:
                d["all_hit"] += 1
            if r["verdict"] == "有价值":
                d["value_n"] += 1
                if r["hit"]:
                    d["value_hit"] += 1
                if r["best_fp"] is not None:
                    d["value_fp_sum"] += r["best_fp"]

    daily = []
    for matchday in sorted(daily_map, reverse=True):
        d = daily_map[matchday]
        daily.append({
            "matchday": d["matchday"],
            "total": d["total"],
            "all_hit": d["all_hit"],
            "all_rate": _rate(d["all_hit"], d["total"]),
            "value_n": d["value_n"],
            "value_hit": d["value_hit"],
            "value_rate": _rate(d["value_hit"], d["value_n"]),
            "value_avg_fp": round(d["value_fp_sum"] / d["value_n"], 4) if d["value_n"] else None,
            "matches": d["matches"],
        })

    # 汇总
    total = sum(d["total"] for d in daily)
    all_hit = sum(d["all_hit"] for d in daily)
    value_n = sum(d["value_n"] for d in daily)
    value_hit = sum(d["value_hit"] for d in daily)
    value_fp_sum = sum(d["value_fp_sum"] for d in daily)
    ou_n = sum(1 for r in records if r["ou_cheap"])
    ou_hit = sum(1 for r in records if r["ou_cheap"] and r["ou_hit"])

    return {
        "days": days,
        "range": [start_day.isoformat(), today.isoformat()],
        "summary": {
            "total": total,
            "all_hit": all_hit,
            "all_rate": _rate(all_hit, total),
            "value_n": value_n,
            "value_hit": value_hit,
            "value_rate": _rate(value_hit, value_n),
            "value_avg_fp": round(value_fp_sum / value_n, 4) if value_n else None,
            "ou_n": ou_n,
            "ou_hit": ou_hit,
            "ou_rate": _rate(ou_hit, ou_n),
        },
        "daily": daily,
    }
