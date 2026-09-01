"""报告相关 API"""
import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta, timezone
from app.db.database import get_db
from app.db.models import Prediction, Match, TeamAlias, League, GoalPickRecord, ColdPickRecord, OddsSnapshot, TeamSeasonStats

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


async def _match_and_update(db: AsyncSession, results: list[dict]) -> tuple[int, list[int]]:
    """
    将赛果匹配到 Prediction 并更新 actual 字段和 result 判定。
    返回 (更新数量, 本次更新涉及的建议 ID 列表)。
    """
    updated = 0
    updated_ids: list[int] = []
    dirty = False  # 是否有任何改动（含半场比分回填）需要 commit

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

        half_home = r.get("half_home_score")
        half_away = r.get("half_away_score")

        # 已结算过的不重复更新 Prediction，但 Match 状态/比分仍需同步（幂等）
        if matched_pred.result_spf != 0:
            m = matched_pred.match
            if m and (m.status != "finished" or m.home_score is None):
                m.status = "finished"
                m.home_score = matched_pred.actual_home_score
                m.away_score = matched_pred.actual_away_score
                dirty = True
            # 补充半场比分（串关半全场腿命中判断依赖）
            if m and (m.half_home_score is None or m.half_away_score is None) and (half_home is not None or half_away is not None):
                m.half_home_score = half_home
                m.half_away_score = half_away
                dirty = True
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

        # 同步更新 Match 状态与比分（此前只回写 Prediction，导致已完场比赛 status 恒为 scheduled，
        # 被预测接口当作待预测场次反复重算覆盖）；同时写入半场比分供串关半全场腿命中判断
        if matched_pred.match:
            matched_pred.match.status = "finished"
            matched_pred.match.home_score = r["home_score"]
            matched_pred.match.away_score = r["away_score"]
            if half_home is not None or half_away is not None:
                matched_pred.match.half_home_score = half_home
                matched_pred.match.half_away_score = half_away

        updated += 1
        updated_ids.append(matched_pred.id)

    if updated > 0 or dirty:
        await db.commit()

    return updated, updated_ids


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
        updated, updated_ids = await _match_and_update(db, results)
    except Exception as e:
        return {"success": False, "message": f"赛果匹配更新失败: {str(e)}", "count": 0}

    return {
        "success": True,
        "message": f"抓取 {len(results)} 条赛果，成功匹配更新 {updated} 场",
        "count": updated,
        "updated_ids": updated_ids,
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
            # 精确命中：SNAP Top2 必须包含 actual_total_goals 的真实值；5 球只有 top2 含 5 才算命中
            if pred.actual_total_goals in snap_for_hit:
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


@router.get("/league-accuracy")
async def get_league_accuracy(
    days: int = Query(30, description="统计近N天，默认30天"),
    db: AsyncSession = Depends(get_db),
):
    """
    近N天按联赛分组的 SNAP Top2 进球数命中率。
    返回按已结算场次降序排列的联赛列表。
    """
    from collections import defaultdict
    from sqlalchemy.orm import joinedload

    today = datetime.now(BEIJING_TZ)
    start = (today - timedelta(days=days)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    end = (today + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)

    result = await db.execute(
        select(Prediction)
        .options(joinedload(Prediction.match).joinedload(Match.league))
        .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end))
    )
    predictions = result.unique().scalars().all()

    # 按联赛分组统计
    by_league: dict[str, dict] = defaultdict(lambda: {"total": 0, "settled": 0, "hit": 0, "miss": 0})

    for pred in predictions:
        match = pred.match
        if not match:
            continue
        lg_name = match.league.name_zh if match.league else "未知联赛"
        by_league[lg_name]["total"] += 1

        if pred.actual_total_goals is not None:
            by_league[lg_name]["settled"] += 1
            snap = pred.snap_top2_c if pred.snap_top2_c else pred.snap_top2
            if snap:
                # 精确命中：actual_total_goals 真实值必须在 snap top2 中
                if pred.actual_total_goals in snap:
                    by_league[lg_name]["hit"] += 1
                else:
                    by_league[lg_name]["miss"] += 1

    # 组装结果，按已结算场次降序
    result_list = []
    for lg_name, stats in by_league.items():
        settled = stats["settled"]
        accuracy = round(stats["hit"] / settled * 100, 1) if settled > 0 else 0.0
        result_list.append({
            "league_name": lg_name,
            "total": stats["total"],
            "settled": settled,
            "hit": stats["hit"],
            "miss": stats["miss"],
            "accuracy": accuracy,
        })

    result_list.sort(key=lambda x: x["settled"], reverse=True)

    return {
        "data": result_list,
        "days": days,
        "date_from": start.date().isoformat(),
        "date_to": (today + timedelta(days=1)).date().isoformat(),
    }


# ── 进球数优选（基于历史 Model C 命中率的评分推荐） ──

# 信号最小样本数：低于该样本量则信号不可信，回退到下一层
_SIGNAL_MIN_N = {
    "cross": 3,     # 联赛 × λ 区间 交叉命中率
    "league": 5,    # 联赛整体命中率
    "bucket": 10,   # λ 区间全局命中率
    "snap": 10,     # SNAP 首位全局命中率
}
# 信号特异性权重：越特异的信号权重越高
_SIGNAL_WEIGHT = {
    "cross": 3.0,
    "league": 2.0,
    "bucket": 1.0,
    "snap": 1.0,
    "global": 0.5,
}
# 单信号样本置信权重上限（min(sqrt(n), _MAX_N_CONF)）
_MAX_N_CONF = 10.0


def _lambda_bucket(egc: float | None) -> str | None:
    """将 Model C 预期进球 λ 划分为区间桶"""
    if egc is None:
        return None
    if egc < 2.0:
        return "<2.0"
    if egc < 2.5:
        return "2.0-2.5"
    if egc < 3.0:
        return "2.5-3.0"
    if egc < 3.5:
        return "3.0-3.5"
    return ">=3.5"


def _build_history_stats(rows) -> dict:
    """由历史已结算预测构建各维命中率统计。

    rows: [(league_name, egc, snap_top2_c, actual_total_goals), ...]
    命中口径：actual_total_goals 的真实值必须落在 snap_top2_c 内（精确匹配，5球只有 snap 含 5 才算命中）。
    """
    from collections import defaultdict

    stats = {
        "global": {"n": 0, "hit": 0, "acc": 0.0},
        "league": defaultdict(lambda: {"n": 0, "hit": 0, "acc": 0.0}),
        "bucket": defaultdict(lambda: {"n": 0, "hit": 0, "acc": 0.0}),
        "cross": defaultdict(lambda: {"n": 0, "hit": 0, "acc": 0.0}),
        "snap": defaultdict(lambda: {"n": 0, "hit": 0, "acc": 0.0}),
    }

    for league_name, egc, snap, act in rows:
        if not snap or len(snap) != 2:
            continue
        if act is None:
            continue
        hit = int(act in snap)

        lg = league_name or "未知联赛"
        bucket = _lambda_bucket(egc)
        snap0 = snap[0]

        stats["global"]["n"] += 1
        stats["global"]["hit"] += hit

        stats["league"][lg]["n"] += 1
        stats["league"][lg]["hit"] += hit

        if bucket is not None:
            stats["bucket"][bucket]["n"] += 1
            stats["bucket"][bucket]["hit"] += hit
            stats["cross"][(lg, bucket)]["n"] += 1
            stats["cross"][(lg, bucket)]["hit"] += hit

        stats["snap"][snap0]["n"] += 1
        stats["snap"][snap0]["hit"] += hit

    for d in (stats["league"], stats["bucket"], stats["cross"], stats["snap"]):
        for s in d.values():
            s["acc"] = round(s["hit"] / s["n"] * 100, 1) if s["n"] else 0.0
    stats["global"]["acc"] = round(stats["global"]["hit"] / stats["global"]["n"] * 100, 1) if stats["global"]["n"] else 0.0

    return stats


def _score_goal_pick(egc: float, snap_top2_c: list, league_name: str, hist: dict) -> tuple[float, list]:
    """对单场比赛计算进球数把握度评分（0-100），返回 (score, signals)。

    信号按特异性逐层收集：交叉 → 联赛 → λ区间 → snap首位 → 全局，
    每个信号按 min(sqrt(n), 10) 做样本置信加权，特异性权重见 _SIGNAL_WEIGHT。
    """
    bucket = _lambda_bucket(egc)
    snap0 = snap_top2_c[0] if snap_top2_c and len(snap_top2_c) >= 2 else None

    signals: list[dict] = []

    cross = hist["cross"].get((league_name, bucket))
    if cross and cross["n"] >= _SIGNAL_MIN_N["cross"]:
        signals.append({"type": "cross", "label": f"{league_name}×{bucket}", "acc": cross["acc"], "n": cross["n"], "weight": _SIGNAL_WEIGHT["cross"]})

    lg_stat = hist["league"].get(league_name)
    if lg_stat and lg_stat["n"] >= _SIGNAL_MIN_N["league"]:
        signals.append({"type": "league", "label": league_name, "acc": lg_stat["acc"], "n": lg_stat["n"], "weight": _SIGNAL_WEIGHT["league"]})

    bucket_stat = hist["bucket"].get(bucket) if bucket else None
    if bucket_stat and bucket_stat["n"] >= _SIGNAL_MIN_N["bucket"]:
        signals.append({"type": "bucket", "label": f"λ {bucket}", "acc": bucket_stat["acc"], "n": bucket_stat["n"], "weight": _SIGNAL_WEIGHT["bucket"]})

    snap_stat = hist["snap"].get(snap0) if snap0 is not None else None
    if snap_stat and snap_stat["n"] >= _SIGNAL_MIN_N["snap"]:
        signals.append({"type": "snap", "label": f"SNAP 首位 {snap0}", "acc": snap_stat["acc"], "n": snap_stat["n"], "weight": _SIGNAL_WEIGHT["snap"]})

    g = hist["global"]
    signals.append({"type": "global", "label": "全局", "acc": g["acc"], "n": g["n"], "weight": _SIGNAL_WEIGHT["global"]})

    num = den = 0.0
    for s in signals:
        conf = min(s["n"] ** 0.5, _MAX_N_CONF)
        num += s["acc"] * s["weight"] * conf
        den += s["weight"] * conf

    score = num / den if den else 0.0
    return score, signals


@router.get("/goal-picks")
async def get_goal_picks(
    date: str | None = None,
    days: int = Query(30, description="历史统计窗口（天）"),
    top_n: int = Query(3, description="返回推荐场次数（持久化，供历史命中率统计）"),
    secondary: int = Query(2, description="额外返回次选场次数（不持久化，仅列表展示）"),
    db: AsyncSession = Depends(get_db),
):
    """
    进球数优选：基于近 N 天 Model C 进球数命中率，对指定比赛日的 scheduled 场次
    逐层评分（联赛×λ区间 → 联赛 → λ区间 → SNAP首位 → 全局），返回把握度最高的 top_n 场。

    参数：
    - date: 目标比赛日 YYYY-MM-DD（默认今天，比赛周期 [12:00, 次日12:00)）
    - days: 历史统计窗口天数，默认 30
    - top_n: 返回推荐场次数（持久化），默认 3
    - secondary: 额外返回次选场次数（仅展示，不持久化），默认 2
    """
    from collections import defaultdict
    from sqlalchemy.orm import joinedload

    # 目标比赛日范围 [date 12:00, date+1 12:00)
    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
        except ValueError:
            d = datetime.now(BEIJING_TZ)
    else:
        d = datetime.now(BEIJING_TZ)
    target_start = d.replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    target_end = (d + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    display_date = d.strftime("%Y-%m-%d")

    # 历史统计窗口
    now_bj = datetime.now(BEIJING_TZ)
    hist_start = (now_bj - timedelta(days=days)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    hist_end = (now_bj + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)

    # 1) 拉取历史已结算预测并统计
    hist_result = await db.execute(
        select(
            League.name_zh,
            Prediction.expected_goals_c,
            Prediction.snap_top2_c,
            Prediction.actual_total_goals,
        )
        .outerjoin(League, League.id == Prediction.league_id)
        .where(
            and_(
                Prediction.kickoff_time >= hist_start,
                Prediction.kickoff_time < hist_end,
                Prediction.actual_total_goals.isnot(None),
                Prediction.snap_top2_c.isnot(None),
            )
        )
    )
    hist = _build_history_stats(hist_result.all())

    # 2) 拉取目标比赛日的 scheduled 场次（含 Model C SNAP）
    now_naive = now_bj.replace(tzinfo=None)
    match_result = await db.execute(
        select(Prediction)
        .join(Match, Match.id == Prediction.match_id)
        .options(
            joinedload(Prediction.match).joinedload(Match.home_team),
            joinedload(Prediction.match).joinedload(Match.away_team),
            joinedload(Prediction.match).joinedload(Match.league),
        )
        .where(
            and_(
                Prediction.kickoff_time >= target_start,
                Prediction.kickoff_time < target_end,
                Prediction.snap_top2_c.isnot(None),
                Match.status == "scheduled",
                Match.kickoff_time >= now_naive,  # 只推荐未开赛场次
            )
        )
    )
    predictions = match_result.unique().scalars().all()

    # 3) 评分
    scored = []
    for pred in predictions:
        match = pred.match
        if not match:
            continue
        home_team = match.home_team.name_zh if match.home_team else (match.home_team_name or "")
        away_team = match.away_team.name_zh if match.away_team else (match.away_team_name or "")
        league_name = match.league.name_zh if match.league else "未知联赛"

        score, signals = _score_goal_pick(
            pred.expected_goals_c,
            pred.snap_top2_c,
            league_name,
            hist,
        )
        scored.append({
            "match_id": pred.match_id,
            "match_num": match.match_num or "",
            "league_name": league_name,
            "home_team": home_team,
            "away_team": away_team,
            "kickoff_time": str(match.kickoff_time),
            "_kickoff_dt": match.kickoff_time,
            "expected_goals_c": pred.expected_goals_c,
            "snap_top2_c": pred.snap_top2_c,
            "score": round(score, 1),
            "signals": signals,
        })

    # 排序：评分降序，平分时按开赛时间早者优先（保证结果确定、可复现）
    scored.sort(key=lambda x: (-x["score"], x["kickoff_time"]))
    top = scored[:top_n]
    secondary_items = scored[top_n:top_n + secondary]

    # 4) 持久化推荐快照（按 pick_date + rank 幂等 upsert，供历史命中率统计）
    if top:
        pick_date_val = d.date()
        for idx, item in enumerate(top):
            rank = idx + 1
            rec_result = await db.execute(
                select(GoalPickRecord).where(
                    and_(
                        GoalPickRecord.pick_date == pick_date_val,
                        GoalPickRecord.rank == rank,
                    )
                )
            )
            rec = rec_result.scalar_one_or_none()
            if rec is None:
                rec = GoalPickRecord(pick_date=pick_date_val, rank=rank)
                db.add(rec)
            rec.match_id = item["match_id"]
            rec.match_num = item["match_num"]
            rec.league_name = item["league_name"]
            rec.home_team = item["home_team"]
            rec.away_team = item["away_team"]
            rec.kickoff_time = item["_kickoff_dt"]
            rec.expected_goals_c = item["expected_goals_c"]
            rec.snap_top2_c = item["snap_top2_c"]
            rec.score = item["score"]
        await db.commit()

    # 清理内部字段后返回（次选不持久化，仅展示）
    for item in top:
        item.pop("_kickoff_dt", None)
    for item in secondary_items:
        item.pop("_kickoff_dt", None)

    return {
        "data": top,
        "secondary": secondary_items,
        "date": display_date,
        "days": days,
        "top_n": top_n,
        "global_accuracy": hist["global"]["acc"],
        "global_n": hist["global"]["n"],
        "total_candidates": len(scored),
    }


@router.get("/goal-picks/history")
async def get_goal_picks_history(
    days: int = Query(30, description="统计近N天"),
    db: AsyncSession = Depends(get_db),
):
    """
    进球数优选历史命中率：按比赛日统计推荐场次的进球数命中情况。

    命中口径与 goal-picks 一致：actual_total_goals 真实值必须落在推荐时的 snap_top2_c 快照内（精确匹配；5 球只有 top2 含 5 才算命中）。
    返回按日期倒序的每日统计 + 累计汇总。
    """
    from collections import defaultdict

    now_bj = datetime.now(BEIJING_TZ)
    start_date = (now_bj - timedelta(days=days)).date()
    end_date = (now_bj + timedelta(days=1)).date()

    result = await db.execute(
        select(GoalPickRecord, Prediction.actual_total_goals)
        .outerjoin(Prediction, Prediction.match_id == GoalPickRecord.match_id)
        .where(
            and_(
                GoalPickRecord.pick_date >= start_date,
                GoalPickRecord.pick_date < end_date,
            )
        )
        .order_by(GoalPickRecord.pick_date.desc(), GoalPickRecord.rank.asc())
    )

    daily: dict[str, dict] = defaultdict(lambda: {"total": 0, "settled": 0, "hit": 0})

    for rec, act in result:
        key = rec.pick_date.isoformat()
        daily[key]["total"] += 1
        if act is not None:
            daily[key]["settled"] += 1
            snap = rec.snap_top2_c
            if isinstance(snap, list) and len(snap) == 2:
                act_capped = min(act, 4)
                if act_capped in snap:
                    daily[key]["hit"] += 1

    data = []
    total_picks = settled = hit = 0
    for date_key in sorted(daily.keys(), reverse=True):
        s = daily[date_key]
        acc = round(s["hit"] / s["settled"] * 100, 1) if s["settled"] else 0.0
        data.append({
            "date": date_key,
            "total": s["total"],
            "settled": s["settled"],
            "hit": s["hit"],
            "accuracy": acc,
        })
        total_picks += s["total"]
        settled += s["settled"]
        hit += s["hit"]

    overall_acc = round(hit / settled * 100, 1) if settled else 0.0

    return {
        "data": data,
        "summary": {
            "total_picks": total_picks,
            "settled": settled,
            "hit": hit,
            "miss": settled - hit,
            "accuracy": overall_acc,
        },
        "days": days,
    }


# ── 冷门优选（排名冲突信号）──

def _implied_from_odds(h, d, a):
    """欧赔去水归一化隐含概率"""
    if not h or not d or not a or min(h, d, a) <= 1.01:
        return None
    ih, id_, ia = 1/h, 1/d, 1/a
    tot = ih + id_ + ia
    return ih/tot, id_/tot, ia/tot


def _build_cold_pick_score(rank_gap: int, fav_prob: float) -> float:
    """冷门优选把握度：排名差距为主（越大越强），热门概率微调

    实证（134场近30天）：冲突场次冷门率 77.8%；|gap|>=3 → 83.3%；>=4 → 88.2%。
    fav_prob 越高说明市场高估越自信，略加分。
    """
    score = float(abs(rank_gap))
    if fav_prob >= 0.55:
        score += 0.5
    return round(score, 1)


@router.get("/cold-picks")
async def get_cold_picks(
    date: str | None = None,
    top_n: int = Query(3, description="返回推荐场次数（持久化，供历史命中率统计）"),
    secondary: int = Query(3, description="额外返回次选场次数（不持久化，仅列表展示）"),
    db: AsyncSession = Depends(get_db),
):
    """
    冷门优选：基于「市场高估偏差」信号挑选最可能爆冷的场次。

    规则（同联赛场次中）：
    - 市场热门方向 = 赛前快照收盘隐含概率 argmax
    - 排名优势方向 = 同联赛排名靠前一方
    - 冲突 = 市场热门方向 与 排名优势方向 相反 → 市场高估 → 冷门风险高

    命中口径（历史统计）：实际赛果 ≠ 推荐时的市场热门方向，即视为「爆冷命中」。
    """
    from collections import defaultdict
    from sqlalchemy.orm import joinedload

    # 目标比赛日范围 [date 12:00, date+1 12:00)
    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
        except ValueError:
            d = datetime.now(BEIJING_TZ)
    else:
        d = datetime.now(BEIJING_TZ)
    target_start = d.replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    target_end = (d + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    display_date = d.strftime("%Y-%m-%d")

    now_naive = datetime.now(BEIJING_TZ).replace(tzinfo=None)

    # 1) 目标比赛日 scheduled 且未开赛场次
    match_result = await db.execute(
        select(Match)
        .options(
            joinedload(Match.home_team),
            joinedload(Match.away_team),
            joinedload(Match.league),
        )
        .where(
            and_(
                Match.kickoff_time >= target_start,
                Match.kickoff_time < target_end,
                Match.status == "scheduled",
                Match.kickoff_time >= now_naive,  # 只推荐未开赛场次
            )
        )
    )
    matches = match_result.scalars().all()
    if not matches:
        return {"data": [], "secondary": [], "date": display_date, "top_n": top_n,
                "total_candidates": 0, "total_conflicts": 0, "message": "无 scheduled 场次"}

    # 2) 赛前快照 → 市场热门方向（严格 snapshot_time < kickoff）
    match_ids = [m.id for m in matches]
    snaps = (await db.execute(
        select(OddsSnapshot).where(OddsSnapshot.match_id.in_(match_ids))
    )).scalars().all()
    by_match = defaultdict(list)
    for s in snaps:
        kt = next((m.kickoff_time for m in matches if m.id == s.match_id), None)
        if kt and s.snapshot_time < kt:
            by_match[s.match_id].append(s)

    def consensus_at(mid, t):
        """指定时刻多家博彩共识隐含概率（每家仅取一条，均值去水归一化）"""
        sl = by_match.get(mid)
        if not sl:
            return None
        spf = {}
        for s in sl:
            if s.snapshot_time != t:
                continue
            bm = s.bookmaker or "unknown"
            if bm not in spf and s.home_win and s.draw and s.away_win:
                spf[bm] = s
        if not spf:
            return None
        return _implied_from_odds(
            sum(s.home_win for s in spf.values()) / len(spf),
            sum(s.draw for s in spf.values()) / len(spf),
            sum(s.away_win for s in spf.values()) / len(spf),
        )

    def times_of(mid):
        sl = by_match.get(mid)
        return sorted({s.snapshot_time for s in sl}) if sl else []

    # 3) 同联赛排名（team_id → position）
    all_tids = set()
    for m in matches:
        if m.home_team_id: all_tids.add(m.home_team_id)
        if m.away_team_id: all_tids.add(m.away_team_id)
    stat_rows = (await db.execute(
        select(TeamSeasonStats).where(TeamSeasonStats.team_id.in_(all_tids))
    )).scalars().all()
    rank_by_team = {}
    for st in stat_rows:
        if st.league_position is not None:
            key = st.team_id
            if key not in rank_by_team or (st.league_id and not rank_by_team[key].get("league_id")):
                rank_by_team[key] = {"position": st.league_position, "league_id": st.league_id}

    # 4) 冲突判定 + 评分（含盘口资金异动特征：开盘→收盘、相邻快照单步）
    scored = []
    for m in matches:
        if not m.home_team_id or not m.away_team_id:
            continue
        times = times_of(m.id)
        if not times:
            continue
        imp = consensus_at(m.id, times[-1])
        if not imp:
            continue
        fav_last = int(max(range(3), key=lambda i: imp[i]))
        if fav_last not in (0, 2):  # 平局为热门时无方向意义，跳过
            continue
        # 盘口资金异动（与探针 _cold_factor_v2 口径一致）
        op_time = next(
            (t for t in times if any(s.is_opening for s in by_match[m.id] if s.snapshot_time == t)),
            times[0],
        )
        imp_open = consensus_at(m.id, op_time)
        odds_delta_max = 0.0
        if imp_open:
            odds_delta_max = max(abs(imp[i] - imp_open[i]) for i in range(3))
        odds_step_max = 0.0
        for i in range(1, len(times)):
            ia = consensus_at(m.id, times[i - 1])
            ib = consensus_at(m.id, times[i])
            if ia and ib:
                odds_step_max = max(odds_step_max, max(abs(ib[j] - ia[j]) for j in range(3)))
        rk_h = rank_by_team.get(m.home_team_id)
        rk_a = rank_by_team.get(m.away_team_id)
        if not (rk_h and rk_a and rk_h.get("league_id") == rk_a.get("league_id") and rk_h.get("league_id")):
            continue
        rank_gap = rk_h["position"] - rk_a["position"]
        support = (fav_last == 0) == (rank_gap < 0)  # 冲突=False
        if support:
            continue
        league_name = m.league.name_zh if m.league else "未知联赛"
        score = _build_cold_pick_score(rank_gap, imp[fav_last])
        scored.append({
            "match_id": m.id,
            "match_num": m.match_num or "",
            "league_name": league_name,
            "home_team": m.home_team.name_zh if m.home_team else (m.home_team_name or ""),
            "away_team": m.away_team.name_zh if m.away_team else (m.away_team_name or ""),
            "kickoff_time": str(m.kickoff_time),
            "_kickoff_dt": m.kickoff_time,
            "fav_dir": fav_last,
            "fav_prob": round(imp[fav_last], 3),
            "implied_probs": {  # 去水归一化隐含概率三元组
                "home": round(imp[0], 3),
                "draw": round(imp[1], 3),
                "away": round(imp[2], 3),
            },
            "cold_dirs": [1, 2] if fav_last == 0 else [0, 1],  # 搏冷方向 = 所有非热门方向（含平局）
            "rank_gap": abs(rank_gap),
            "odds_delta_max": round(odds_delta_max, 4),  # 盘口资金异动：开盘→收盘最大概率变动（待验证叠加特征）
            "odds_step_max": round(odds_step_max, 4),    # 盘口资金异动：相邻快照最大单步跳变（待验证叠加特征）
            "score": score,
            "signals": [
                f"市场热门={('主胜' if fav_last == 0 else '客胜')}({imp[fav_last]:.0%})，"
                f"但{('客队' if rank_gap > 0 else '主队')}排名领先 {abs(rank_gap)} 位",
            ],
        })

    # 排序：评分降序 → 开赛早优先（确定性）
    scored.sort(key=lambda x: (-x["score"], x["kickoff_time"]))
    top = scored[:top_n]
    secondary_items = scored[top_n:top_n + secondary]

    # 5) 持久化推荐快照（按 pick_date + rank 幂等 upsert）
    if top:
        pick_date_val = d.date()
        for idx, item in enumerate(top):
            rank = idx + 1
            rec_result = await db.execute(
                select(ColdPickRecord).where(
                    and_(
                        ColdPickRecord.pick_date == pick_date_val,
                        ColdPickRecord.rank == rank,
                    )
                )
            )
            rec = rec_result.scalar_one_or_none()
            if rec is None:
                rec = ColdPickRecord(pick_date=pick_date_val, rank=rank)
                db.add(rec)
            rec.match_id = item["match_id"]
            rec.match_num = item["match_num"]
            rec.league_name = item["league_name"]
            rec.home_team = item["home_team"]
            rec.away_team = item["away_team"]
            rec.kickoff_time = item["_kickoff_dt"]
            rec.fav_dir = item["fav_dir"]
            rec.fav_prob = item["fav_prob"]
            rec.rank_gap = item["rank_gap"]
            rec.odds_delta_max = item["odds_delta_max"]
            rec.odds_step_max = item["odds_step_max"]
            rec.score = item["score"]
        await db.commit()

    for item in top:
        item.pop("_kickoff_dt", None)
    for item in secondary_items:
        item.pop("_kickoff_dt", None)

    return {
        "data": top,
        "secondary": secondary_items,
        "date": display_date,
        "top_n": top_n,
        "total_candidates": len(scored),
        "total_conflicts": len(scored),
    }


@router.get("/cold-picks/history")
async def get_cold_picks_history(
    days: int = Query(30, description="统计近N天"),
    db: AsyncSession = Depends(get_db),
):
    """
    冷门优选历史命中率：按比赛日统计推荐场次的爆冷命中情况。

    命中口径与 cold-picks 一致：实际赛果 ≠ 推荐时的市场热门方向 → 爆冷命中。
    """
    from collections import defaultdict

    now_bj = datetime.now(BEIJING_TZ)
    start_date = (now_bj - timedelta(days=days)).date()
    end_date = (now_bj + timedelta(days=1)).date()

    result = await db.execute(
        select(ColdPickRecord, Prediction.actual_home_score, Prediction.actual_away_score)
        .outerjoin(Prediction, Prediction.match_id == ColdPickRecord.match_id)
        .where(
            and_(
                ColdPickRecord.pick_date >= start_date,
                ColdPickRecord.pick_date < end_date,
            )
        )
        .order_by(ColdPickRecord.pick_date.desc(), ColdPickRecord.rank.asc())
    )

    daily: dict[str, dict] = defaultdict(lambda: {"total": 0, "settled": 0, "hit": 0})

    for rec, hs, as_ in result:
        key = rec.pick_date.isoformat()
        daily[key]["total"] += 1
        if hs is not None and as_ is not None:
            daily[key]["settled"] += 1
            actual = 0 if hs > as_ else (1 if hs == as_ else 2)
            # 爆冷命中 = 实际 ≠ 推荐时市场热门方向（且热门为主/客）
            if rec.fav_dir in (0, 2) and actual != rec.fav_dir:
                daily[key]["hit"] += 1

    data = []
    total_picks = settled = hit = 0
    for date_key in sorted(daily.keys(), reverse=True):
        s = daily[date_key]
        acc = round(s["hit"] / s["settled"] * 100, 1) if s["settled"] else 0.0
        data.append({
            "date": date_key,
            "total": s["total"],
            "settled": s["settled"],
            "hit": s["hit"],
            "accuracy": acc,
        })
        total_picks += s["total"]
        settled += s["settled"]
        hit += s["hit"]

    overall_acc = round(hit / settled * 100, 1) if settled else 0.0

    return {
        "data": data,
        "summary": {
            "total_picks": total_picks,
            "settled": settled,
            "hit": hit,
            "miss": settled - hit,
            "accuracy": overall_acc,
        },
        "days": days,
    }
