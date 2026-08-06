"""球队相关 API"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.database import get_db
from app.db.models import Team, TeamSeasonStats, HeadToHead

router = APIRouter(prefix="/api/teams", tags=["teams"])


def _calc_radar(stats: TeamSeasonStats | None, h2h_stats: dict | None = None, recent_wins: int = 0, recent_total: int = 0) -> dict:
    """根据赛季统计数据 + H2H 交锋统计 + 近期战绩 计算六维雷达值（0-100）
    
    stats: TeamSeasonStats (赛季聚合)
    h2h_stats: H2H 平均统计 {"216": shots, "219": on_target, "209": possession, "315": attacks, "316": danger}
    recent_wins/recent_total: 最近10场胜场数/总场数（用于 form 维度）
    """
    h2h = h2h_stats or {}
    played = stats.played if stats else 0
    played_for_radar = max(played, 1)

    # ── 进攻：优先 H2H shots + dangerous_attacks ──
    attack = 50
    if h2h:
        shots = h2h.get("shots", 0)
        danger = h2h.get("dangerous", 0)
        if shots > 0 or danger > 0:
            attack = min(100, max(10, shots * 4 + danger * 1.5))
    elif stats and stats.goals_for:
        attack = min(100, max(10, (stats.goals_for / played_for_radar) * 60))

    # ── 防守：赛季 goals_against，H2H 无直接防守数据 ──
    defense = 50
    if stats and stats.goals_against:
        ga_ratio = stats.goals_against / played_for_radar
        defense = min(100, max(10, 100 - ga_ratio * 30))

    # ── 控球：优先 H2H，否则赛季 ──
    possession = h2h.get("possession") or (stats.avg_possession if stats else None) or 50

    # ── xG：优先 H2H，否则赛季数据 ──
    xg_val = None
    if h2h and "xG" in h2h:
        xg_val = h2h["xG"]
    if xg_val is None:
        xg_val = (stats.xG if stats else None) or 1.0
    xg_radar = min(100, max(10, float(xg_val) * 50))

    # ── 状态：优先 recent_matches (最近10场真实胜率)，否则赛季 ──
    form_val = 50
    if recent_total >= 5:
        form_val = min(100, max(10, (recent_wins / recent_total) * 100))
    elif stats and stats.wins and stats.played:
        form_val = min(100, max(10, (stats.wins / stats.played) * 100))

    # ── 阵容强度：H2H 射正率 + saves ──
    squad_val = 50
    if h2h:
        on_target = h2h.get("shots_on_target", 0)
        total_shots = h2h.get("shots", 1)
        saves = h2h.get("saves", 0)
        if total_shots > 0:
            squad_val = min(100, max(20, (on_target / total_shots) * 200 + saves * 3))
    elif stats and stats.failed_to_score and played_for_radar > 0:
        squad_val = min(100, max(20, 100 - stats.failed_to_score / played_for_radar * 50))

    return {
        "attack": round(attack, 1),
        "defense": round(defense, 1),
        "possession": round(possession, 1),
        "xG": round(xg_radar, 1),
        "form": round(form_val, 1),
        "squad": round(squad_val, 1),
    }


def _gen_form_str(stats: TeamSeasonStats | None) -> list:
    """生成近期状态 W/D/L 序列，优先用 form 字段（来自 SportMonks latest 真实比赛数据）"""
    if stats and stats.form:
        return list(stats.form)
    return ["-"] * 6


@router.get("/{team_id}/radar")
async def get_team_radar(team_id: int, db: AsyncSession = Depends(get_db)):
    """球队六维雷达图数据"""
    result = await db.execute(select(TeamSeasonStats).where(
        TeamSeasonStats.team_id == team_id
    ).order_by(TeamSeasonStats.season.desc()).limit(1))
    stats = result.scalar_one_or_none()
    # 从 recent_matches 算最近胜场
    rw, rt = 0, 0
    if stats and stats.recent_matches:
        for m in stats.recent_matches:
            rt += 1
            if m.get("result") == "W": rw += 1
    return {"data": _calc_radar(stats, recent_wins=rw, recent_total=rt)}


@router.get("/{team_id}/form")
async def get_team_form(team_id: int, db: AsyncSession = Depends(get_db)):
    """球队近期状态"""
    result = await db.execute(select(TeamSeasonStats).where(
        TeamSeasonStats.team_id == team_id
    ).order_by(TeamSeasonStats.season.desc()).limit(1))
    stats = result.scalar_one_or_none()
    return {"data": {"form": _gen_form_str(stats)}}


@router.get("/match/{match_id}/comparison")
async def get_match_team_comparison(match_id: int, db: AsyncSession = Depends(get_db)):
    """获取一场比赛两队的数据对比（雷达 + 状态 + H2H 统计聚合）"""
    from app.db.models import Match
    match_result = await db.execute(
        select(Match).where(Match.id == match_id)
    )
    match = match_result.scalar_one_or_none()
    if not match:
        return {"error": "not found"}, 404

    home_id = match.home_team_id
    away_id = match.away_team_id

    # ── 拉取 H2H 聚合统计数据（2年内的交锋记录）──
    h2h_agg = {"home": {}, "away": {}}
    two_years_ago = datetime.utcnow() - timedelta(days=730)
    if home_id and away_id:
        h2h_result = await db.execute(
            select(HeadToHead).where(
                ((HeadToHead.home_team_id == home_id) & (HeadToHead.away_team_id == away_id)) |
                ((HeadToHead.home_team_id == away_id) & (HeadToHead.away_team_id == home_id)),
                HeadToHead.match_date >= two_years_ago,
            ).order_by(HeadToHead.match_date.desc())
        )
        for h2h in h2h_result.scalars().all():
            if h2h.home_team_id == home_id:
                h_stats, a_stats = h2h.home_stats, h2h.away_stats
            else:
                h_stats, a_stats = h2h.away_stats, h2h.home_stats

            for stats_dict, side in [(h_stats, "home"), (a_stats, "away")]:
                if not isinstance(stats_dict, dict):
                    continue
                for k, v in stats_dict.items():
                    h2h_agg[side].setdefault(k, []).append(v)

    # 计算 H2H 平均值
    h2h_avg = {"home": {}, "away": {}}
    for side in ["home", "away"]:
        for k, vals in h2h_agg[side].items():
            if vals:
                h2h_avg[side][k] = sum(vals) / len(vals)

    data = {"home": None, "away": None, "h2h_avg": h2h_avg}
    for side, tid in [("home", home_id), ("away", away_id)]:
        if not tid:
            continue
        result = await db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid)
            .order_by(TeamSeasonStats.season.desc()).limit(1)
        )
        stats = result.scalar_one_or_none()
        team_result = await db.execute(select(Team).where(Team.id == tid))
        team = team_result.scalar_one_or_none()

        # 从实际展示的比赛计算胜平负
        recent = stats.recent_matches if stats and stats.recent_matches else []
        display_w = sum(1 for m in recent if m.get("result") == "W")
        display_d = sum(1 for m in recent if m.get("result") == "D")
        display_l = sum(1 for m in recent if m.get("result") == "L")

        h2h_side = h2h_avg.get(side, {})
        radar = _calc_radar(stats, h2h_side, display_w, display_w + display_d + display_l)
        form = _gen_form_str(stats)

        # 翻译 recent_matches 的对手名（SportMonks 英文名 → DB 中文名）
        if recent:
            sm_ids = [m.get("opponent_sm_id") for m in recent if m.get("opponent_sm_id")]
            if sm_ids:
                name_rows = await db.execute(
                    select(Team.sportmonks_id, Team.name_zh).where(Team.sportmonks_id.in_(sm_ids))
                )
                name_map = {row[0]: row[1] for row in name_rows if row[1]}
                for m in recent:
                    sid = m.get("opponent_sm_id")
                    if sid and sid in name_map:
                        m["opponent"] = name_map[sid]

        # 主/客场战绩统计（兼容 is_home 布尔 和 venue "H"/"A" 两种格式）
        def _is_home(m):
            if m.get("is_home") is not None:
                return bool(m.get("is_home"))
            venue = m.get("venue", "")
            return isinstance(venue, str) and venue.upper() == "H"

        home_w = sum(1 for m in recent if m.get("result") == "W" and _is_home(m))
        home_d = sum(1 for m in recent if m.get("result") == "D" and _is_home(m))
        home_l = sum(1 for m in recent if m.get("result") == "L" and _is_home(m))
        away_w = sum(1 for m in recent if m.get("result") == "W" and not _is_home(m))
        away_d = sum(1 for m in recent if m.get("result") == "D" and not _is_home(m))
        away_l = sum(1 for m in recent if m.get("result") == "L" and not _is_home(m))

        data[side] = {
            "team_id": tid,
            "name": team.name_zh if team else "",
            "radar": radar,
            "form": form,
            "recent_matches": recent,
            "stats": {
                "played": display_w + display_d + display_l,
                "wins": display_w,
                "draws": display_d,
                "losses": display_l,
                "home": {
                    "wins": home_w,
                    "draws": home_d,
                    "losses": home_l,
                    "played": home_w + home_d + home_l,
                },
                "away": {
                    "wins": away_w,
                    "draws": away_d,
                    "losses": away_l,
                    "played": away_w + away_d + away_l,
                },
                "goals_for": stats.goals_for if stats else 0,
                "goals_against": stats.goals_against if stats else 0,
                "avg_possession": stats.avg_possession if stats else None,
                "xG": stats.xG if stats else None,
                "xGA": stats.xGA if stats else None,
            } if stats else None,
        }

    return {"data": data}
