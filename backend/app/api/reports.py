"""报告相关 API"""
import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta, timezone
from app.db.database import get_db
from app.db.models import Prediction, Match, TeamAlias

router = APIRouter(prefix="/api/reports", tags=["reports"])

BEIJING_TZ = timezone(timedelta(hours=8))

RESULT_API = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"


def _yesterday_range():
    """上一日竞彩比赛日范围 [昨日12:00, 今日12:00)，返回 naive datetime"""
    today = datetime.now(BEIJING_TZ)
    yesterday = today - timedelta(days=1)
    start = yesterday.replace(hour=12, minute=0, second=0, microsecond=0)
    end = today.replace(hour=12, minute=0, second=0, microsecond=0)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


def _date_range(date_str: str | None = None):
    """
    根据日期字符串计算竞彩比赛日范围 [date 12:00, date+1 12:00)
    不传则默认使用昨日范围
    """
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
        except ValueError:
            d = datetime.now(BEIJING_TZ) - timedelta(days=1)
    else:
        d = datetime.now(BEIJING_TZ) - timedelta(days=1)
    start = d.replace(hour=12, minute=0, second=0, microsecond=0)
    end = (d + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    return start.replace(tzinfo=None), end.replace(tzinfo=None), d.strftime("%Y-%m-%d")


def _map_confidence(level: str | None) -> float:
    mapping = {"high": 85.0, "medium": 65.0, "low": 45.0}
    return mapping.get(level, 50.0) if level else 50.0


def _match_num_sort_key(match_num: str):
    """从 match_num 中提取可排序的键值，如 '周三006'→6, '3006'→3006"""
    import re
    m = re.search(r'(\d+)', match_num)
    return int(m.group(1)) if m else 0


# ── 赛果抓取 ──


async def _scrape_sporttery_results(start_date: str, end_date: str) -> list[dict]:
    """从竞彩网 JSON API 抓取指定日期范围的赛果"""
    all_results = []
    page_no = 1

    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            url = (
                f"{RESULT_API}?matchBeginDate={start_date}&matchEndDate={end_date}"
                f"&leagueId=&pageSize=50&pageNo={page_no}"
                f"&isFix=0&matchPage=1&pcOrWap=1"
            )
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://www.sporttery.cn/jc/zqsgkj/",
            })
            resp.raise_for_status()
            data = resp.json()

            if str(data.get("errorCode")) != "0":
                break

            val = data.get("value", {})
            matches = val.get("matchResult", [])

            for m in matches:
                if str(m.get("matchResultStatus")) != "2":
                    continue  # 仅处理已完场
                full_score = m.get("sectionsNo999", "")
                if not full_score:
                    continue
                half_score = m.get("sectionsNo1", "")
                home_score, away_score = _parse_score(full_score)
                half_home, half_away = _parse_score(half_score) if half_score else (None, None)

                if home_score is None:
                    continue

                try:
                    handicap = float(m.get("goalLine", "0"))
                except (ValueError, TypeError):
                    handicap = 0.0

                all_results.append({
                    "date": m.get("matchDate", ""),
                    "match_num": m.get("matchNumStr", ""),
                    "league_name": m.get("leagueNameAbbr", ""),
                    "home_team": m.get("homeTeam", ""),
                    "away_team": m.get("awayTeam", ""),
                    "home_team_full": m.get("allHomeTeam", ""),
                    "away_team_full": m.get("allAwayTeam", ""),
                    "handicap": handicap,
                    "half_home_score": half_home,
                    "half_away_score": half_away,
                    "home_score": home_score,
                    "away_score": away_score,
                })

            total_pages = int(val.get("pages", 1))
            if page_no >= total_pages:
                break
            page_no += 1

    return all_results


def _parse_score(score_str: str) -> tuple[int | None, int | None]:
    """解析 '4:0' → (4, 0)"""
    import re
    m = re.match(r'(\d+)\s*[:：]\s*(\d+)', score_str)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _judge_spf(home_score: int, away_score: int, home_prob: float, draw_prob: float, away_prob: float) -> int:
    """判定胜平负命中: 1=中, -1=不中"""
    if home_score > away_score:
        actual = "home"
    elif home_score < away_score:
        actual = "away"
    else:
        actual = "draw"

    probs = {"home": home_prob or 0, "draw": draw_prob or 0, "away": away_prob or 0}
    predicted = max(probs, key=probs.get)
    return 1 if predicted == actual else -1


def _judge_handicap(
    home_score: int, away_score: int, handicap_line: float | None,
    h_home_prob: float, h_draw_prob: float, h_away_prob: float
) -> int:
    """判定让球胜平负命中"""
    if handicap_line is None:
        return 0
    adj_home = home_score + handicap_line
    if adj_home > away_score:
        actual = "home"
    elif adj_home < away_score:
        actual = "away"
    else:
        actual = "draw"

    probs = {"home": h_home_prob or 0, "draw": h_draw_prob or 0, "away": h_away_prob or 0}
    predicted = max(probs, key=probs.get)
    return 1 if predicted == actual else -1


def _judge_goals(total_goals: int, snap_top2_data: list | None) -> int:
    """判定进球数命中：实际总进球是否在存储的 SNAP Top2 范围内"""
    if not snap_top2_data or len(snap_top2_data) < 2:
        return 0
    return 1 if total_goals in snap_top2_data else -1


def _judge_score(actual_score: str, score_top5: list | None) -> int:
    """判定比分命中: 实际比分是否在Top5中"""
    if not score_top5:
        return 0
    for s in score_top5[:5]:
        if isinstance(s, dict) and s.get("score") == actual_score:
            return 1
    return -1


async def _match_and_update(db: AsyncSession, results: list[dict]) -> int:
    """
    将赛果匹配到 Prediction 并更新 actual 字段和 result 判定。
    返回更新数量。
    """
    updated = 0

    for r in results:
        home_name = r["home_team"]
        away_name = r["away_team"]
        home_full = r.get("home_team_full", "")
        away_full = r.get("away_team_full", "")
        target_date = r["date"]

        try:
            d = datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            continue
        day_start = d
        day_end = d + timedelta(days=1)

        from sqlalchemy.orm import joinedload

        query_result = await db.execute(
            select(Prediction)
            .options(
                joinedload(Prediction.match)
                .joinedload(Match.home_team),
                joinedload(Prediction.match)
                .joinedload(Match.away_team),
            )
            .where(
                and_(
                    Prediction.kickoff_time >= day_start,
                    Prediction.kickoff_time < day_end,
                )
            )
        )
        predictions = query_result.unique().scalars().all()

        # 收集所有涉及球队的别名
        team_ids = set()
        for pred in predictions:
            match = pred.match
            if match:
                if match.home_team_id:
                    team_ids.add(match.home_team_id)
                if match.away_team_id:
                    team_ids.add(match.away_team_id)
        team_aliases: dict[int, list[str]] = {}
        if team_ids:
            alias_result = await db.execute(
                select(TeamAlias.team_id, TeamAlias.alias_name).where(
                    TeamAlias.team_id.in_(team_ids)
                )
            )
            for tid, aname in alias_result:
                if aname:
                    team_aliases.setdefault(tid, []).append(aname)

        matched_pred = None
        for pred in predictions:
            match = pred.match
            if not match:
                continue
            m_home = match.home_team.name_zh if match.home_team else (match.home_team_name or "")
            m_away = match.away_team.name_zh if match.away_team else (match.away_team_name or "")

            # 收集主客队的全部已知名称（正式名 + 别名）
            home_names = [m_home] + team_aliases.get(match.home_team_id or 0, [])
            away_names = [m_away] + team_aliases.get(match.away_team_id or 0, [])

            # 队名匹配（简称和全称都尝试）
            matched = False
            for scraped_home in (home_name, home_full):
                if not scraped_home:
                    continue
                for scraped_away in (away_name, away_full):
                    if not scraped_away:
                        continue
                    if (any(_teams_match(h, scraped_home) for h in home_names) and
                            any(_teams_match(a, scraped_away) for a in away_names)):
                        matched = True
                        break
                if matched:
                    break

            if matched:
                matched_pred = pred
                break

        if not matched_pred:
            continue

        # 已结算过的不重复更新
        if matched_pred.result_spf != 0:
            continue

        actual_score_str = f"{r['home_score']}:{r['away_score']}"
        total_goals = r["home_score"] + r["away_score"]

        # 回写 Prediction
        matched_pred.actual_home_score = r["home_score"]
        matched_pred.actual_away_score = r["away_score"]
        matched_pred.actual_total_goals = total_goals
        matched_pred.actual_score = actual_score_str

        matched_pred.result_spf = _judge_spf(
            r["home_score"], r["away_score"],
            matched_pred.home_prob, matched_pred.draw_prob, matched_pred.away_prob
        )
        matched_pred.result_hcp = _judge_handicap(
            r["home_score"], r["away_score"],
            matched_pred.match.handicap_line if matched_pred.match else None,
            matched_pred.handicap_home_prob, matched_pred.handicap_draw_prob, matched_pred.handicap_away_prob
        )
        matched_pred.result_goals = _judge_goals(total_goals, matched_pred.snap_top2)
        matched_pred.result_score = _judge_score(actual_score_str, matched_pred.score_top5_json)

        updated += 1

    if updated > 0:
        await db.commit()

    return updated


def _teams_match(db_name: str, scraped_name: str) -> bool:
    """队名匹配：完全一致 → 子串包含 → 共享2+连续字符 → 已知别名映射"""
    if not db_name or not scraped_name:
        return False
    db_clean = db_name.strip()
    sc_clean = scraped_name.strip()
    if db_clean == sc_clean:
        return True

    # 竞彩网特有队名 → 我们数据库队名的映射
    # 当 sporttery 返回的队名与数据库队名完全不同时使用
    _KNOWN_ALIASES = {
        "坦山猫": "埃尔维斯",
    }
    resolved = _KNOWN_ALIASES.get(sc_clean, sc_clean)

    if db_clean == resolved:
        return True
    if len(db_clean) >= 2 and len(resolved) >= 2:
        if db_clean in resolved or resolved in db_clean:
            return True
    # 模糊匹配：检查是否有 >= 2 个连续相同字符
    for i in range(len(db_clean) - 1):
        if db_clean[i:i+2] in resolved:
            return True
    for i in range(len(resolved) - 1):
        if resolved[i:i+2] in db_clean:
            return True
    return False


# ── API 端点 ──


@router.get("/daily/summary")
async def get_daily_summary(date: str | None = None, db: AsyncSession = Depends(get_db)):
    """每日报告概览统计，支持按日期查询历史"""
    start, end, display_date = _date_range(date)

    total_result = await db.execute(
        select(func.count(Prediction.id)).where(
            and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end)
        )
    )
    total_matches = total_result.scalar() or 0

    if total_matches == 0:
        return {
            "data": {
                "display_date": display_date,
                "total_matches": 0, "cold_match_count": 0,
                "avg_confidence": 0.0, "model_version": "v0.1.0",
                "league_count": 0, "settled_count": 0,
                "spf_hit": 0, "hcp_hit": 0, "goals_hit": 0,
            }
        }

    cold_result = await db.execute(
        select(func.count(Prediction.id)).where(
            and_(
                Prediction.kickoff_time >= start,
                Prediction.kickoff_time < end,
                Prediction.is_cold_match == True
            )
        )
    )
    cold_match_count = cold_result.scalar() or 0

    result = await db.execute(
        select(Prediction.confidence_level).where(
            and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end)
        )
    )
    levels = result.scalars().all()
    avg_confidence = sum(_map_confidence(l) for l in levels) / len(levels) if levels else 0.0

    league_result = await db.execute(
        select(func.count(func.distinct(Prediction.league_id))).where(
            and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end)
        )
    )
    league_count = league_result.scalar() or 0

    version_result = await db.execute(
        select(Prediction.model_version).where(
            and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end)
        ).limit(1)
    )
    model_version = version_result.scalar() or "v0.1.0"

    # 已结算统计
    settled_result = await db.execute(
        select(func.count(Prediction.id)).where(
            and_(
                Prediction.kickoff_time >= start,
                Prediction.kickoff_time < end,
                Prediction.result_spf != 0
            )
        )
    )
    settled_count = settled_result.scalar() or 0

    spf_hit = hcp_hit = goals_hit = 0
    if settled_count > 0:
        spf_hit_result = await db.execute(
            select(func.count(Prediction.id)).where(
                and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end, Prediction.result_spf == 1)
            )
        )
        spf_hit = spf_hit_result.scalar() or 0
        hcp_hit_result = await db.execute(
            select(func.count(Prediction.id)).where(
                and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end, Prediction.result_hcp == 1)
            )
        )
        hcp_hit = hcp_hit_result.scalar() or 0
        goals_hit_result = await db.execute(
            select(func.count(Prediction.id)).where(
                and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end, Prediction.result_goals == 1)
            )
        )
        goals_hit = goals_hit_result.scalar() or 0

    return {
        "data": {
            "display_date": display_date,
            "total_matches": total_matches,
            "cold_match_count": cold_match_count,
            "avg_confidence": round(avg_confidence, 1),
            "model_version": model_version,
            "league_count": league_count,
            "settled_count": settled_count,
            "spf_hit": spf_hit,
            "hcp_hit": hcp_hit,
            "goals_hit": goals_hit,
        }
    }


@router.get("/daily")
async def get_daily_report(date: str | None = None, db: AsyncSession = Depends(get_db)):
    """完整预测报告数据，支持按日期查询历史"""
    from sqlalchemy.orm import joinedload

    start, end, _ = _date_range(date)

    result = await db.execute(
        select(Prediction)
        .options(
            joinedload(Prediction.match)
            .joinedload(Match.home_team),
            joinedload(Prediction.match)
            .joinedload(Match.away_team),
            joinedload(Prediction.match)
            .joinedload(Match.league),
        )
        .where(
            and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end)
        )
    )
    predictions = result.unique().scalars().all()

    data = []
    for pred in predictions:
        match = pred.match
        if match:
            home_team = match.home_team.name_zh if match.home_team else (match.home_team_name or "")
            away_team = match.away_team.name_zh if match.away_team else (match.away_team_name or "")
            league_name = match.league.name_zh if match.league else ""
            handicap_line = match.handicap_line
            match_num = match.match_num or ""
        else:
            home_team = ""
            away_team = ""
            league_name = ""
            handicap_line = None
            match_num = ""

        reference_score = ""
        if pred.score_top5_json and len(pred.score_top5_json) > 0:
            reference_score = pred.score_top5_json[0].get("score", "")

        # 胜平负预测方向
        spf_probs = {"home": pred.home_prob or 0, "draw": pred.draw_prob or 0, "away": pred.away_prob or 0}
        spf_direction = max(spf_probs, key=spf_probs.get)

        # 让球预测方向
        hcp_probs = {"home": pred.handicap_home_prob or 0, "draw": pred.handicap_draw_prob or 0, "away": pred.handicap_away_prob or 0}
        hcp_direction = max(hcp_probs, key=hcp_probs.get)

        data.append({
            "id": pred.id,
            "match_id": pred.match_id,
            "match_num": match_num,
            "kickoff_time": str(pred.kickoff_time),
            "league_name": league_name,
            "home_team": home_team,
            "away_team": away_team,
            "handicap_line": handicap_line,
            "home_prob": pred.home_prob,
            "draw_prob": pred.draw_prob,
            "away_prob": pred.away_prob,
            "spf_direction": spf_direction,
            "handicap_home_prob": pred.handicap_home_prob,
            "handicap_draw_prob": pred.handicap_draw_prob,
            "handicap_away_prob": pred.handicap_away_prob,
            "hcp_direction": hcp_direction,
            "expected_goals": pred.expected_goals,
            "over_2_5_prob": pred.over_2_5_prob,
            "goal_distribution": pred.goal_distribution,
            "snap_top2": pred.snap_top2,
            "expected_goals_c": pred.expected_goals_c,
            "snap_top2_c": pred.snap_top2_c,
            "score_top5_json": pred.score_top5_json,
            "reference_score": reference_score,
            "is_cold_match": pred.is_cold_match,
            "cold_correction": pred.cold_correction,
            "is_hot_match": (pred.confidence_level == "high" and not pred.is_cold_match),
            "confidence_level": pred.confidence_level,
            "model_version": pred.model_version,
            "summary_text": pred.summary_text,
            "risk_warning": pred.risk_warning,
            # 实际赛果
            "actual_home_score": pred.actual_home_score,
            "actual_away_score": pred.actual_away_score,
            "actual_total_goals": pred.actual_total_goals,
            "actual_score": pred.actual_score,
            "result_spf": pred.result_spf,
            "result_hcp": pred.result_hcp,
            "result_goals": pred.result_goals,
            "result_score": pred.result_score,
        })

    # 按 match_num 正序排列
    data.sort(key=lambda x: _match_num_sort_key(x.get("match_num", "")))

    return {"data": data}


@router.post("/update-results")
async def update_results(date: str | None = None, db: AsyncSession = Depends(get_db)):
    """从竞彩网抓取赛果并回写 Prediction，支持指定日期"""
    # 比赛日周期 [date 12:00, date+1 12:00)，抓取覆盖跨两个自然日的赛果
    start, end, _ = _date_range(date)
    start_date = start.strftime("%Y-%m-%d")
    end_date = end.strftime("%Y-%m-%d")

    try:
        results = await _scrape_sporttery_results(start_date, end_date)
    except Exception as e:
        return {"success": False, "message": f"赛果抓取失败: {str(e)}", "count": 0}

    if not results:
        return {"success": True, "message": "未抓取到赛果数据", "count": 0}

    try:
        updated = await _match_and_update(db, results)
    except Exception as e:
        return {"success": False, "message": f"赛果匹配更新失败: {str(e)}", "count": 0}

    return {
        "success": True,
        "message": f"抓取 {len(results)} 条赛果，成功匹配更新 {updated} 场",
        "count": updated,
    }


@router.get("/export/csv")
async def export_csv(db: AsyncSession = Depends(get_db)):
    """导出 CSV"""
    return {"message": "CSV export endpoint"}


@router.get("/range")
async def get_report_range(
    date_from: str,
    date_to: str,
    league_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    按日期范围查询历史预测报告（含统计概要）。
    
    参数：
    - date_from: 起始日期 YYYY-MM-DD（比赛日 12:00 起）
    - date_to: 结束日期 YYYY-MM-DD（至次日 12:00 止）
    - league_id: 可选联赛筛选
    
    返回 data（预测记录列表）+ summary（统计概要）
    """
    from sqlalchemy.orm import joinedload

    # 日期范围：date_from 12:00 ~ (date_to+1) 12:00
    try:
        d_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
        d_to = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
    except ValueError:
        return {"data": [], "summary": {"total": 0, "settled": 0, "goals_hit": 0, "goals_miss": 0, "goals_accuracy": 0.0}}

    start = d_from.replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    end = (d_to + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)

    conditions = [Prediction.kickoff_time >= start, Prediction.kickoff_time < end]
    if league_id is not None:
        conditions.append(Prediction.league_id == league_id)

    result = await db.execute(
        select(Prediction)
        .options(
            joinedload(Prediction.match)
            .joinedload(Match.home_team),
            joinedload(Prediction.match)
            .joinedload(Match.away_team),
            joinedload(Prediction.match)
            .joinedload(Match.league),
        )
        .where(and_(*conditions))
    )
    predictions = result.unique().scalars().all()

    data = []
    settled = 0
    goals_hit = 0
    goals_miss = 0

    for pred in predictions:
        match = pred.match
        if match:
            home_team = match.home_team.name_zh if match.home_team else (match.home_team_name or "")
            away_team = match.away_team.name_zh if match.away_team else (match.away_team_name or "")
            league_name = match.league.name_zh if match.league else ""
        else:
            home_team = ""
            away_team = ""
            league_name = ""

        data.append({
            "id": pred.id,
            "match_id": pred.match_id,
            "kickoff_time": str(pred.kickoff_time),
            "league_name": league_name,
            "league_id": pred.league_id,
            "home_team": home_team,
            "away_team": away_team,
            "expected_goals": pred.expected_goals,
            "snap_top2": pred.snap_top2,
            "expected_goals_c": pred.expected_goals_c,
            "snap_top2_c": pred.snap_top2_c,
            "confidence_level": pred.confidence_level,
            "is_cold_match": pred.is_cold_match,
            "actual_home_score": pred.actual_home_score,
            "actual_away_score": pred.actual_away_score,
            "actual_total_goals": pred.actual_total_goals,
            "actual_score": pred.actual_score,
            "result_goals": pred.result_goals,
        })

        # 基于 Model C 的 SNAP Top2 计算命中（有C用C，否则用B）
        snap_for_hit = pred.snap_top2_c if pred.snap_top2_c else pred.snap_top2
        if pred.actual_total_goals is not None and snap_for_hit:
            settled += 1
            act_capped = min(pred.actual_total_goals, 4)
            if act_capped in snap_for_hit:
                goals_hit += 1
            else:
                goals_miss += 1

    data.sort(key=lambda x: x.get("kickoff_time", ""), reverse=True)

    return {
        "data": data,
        "summary": {
            "total": len(predictions),
            "settled": settled,
            "goals_hit": goals_hit,
            "goals_miss": goals_miss,
            "goals_accuracy": round(goals_hit / settled * 100, 1) if settled > 0 else 0.0,
        },
        "date_from": date_from,
        "date_to": date_to,
    }
