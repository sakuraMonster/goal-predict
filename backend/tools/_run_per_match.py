"""逐场更新 08-15/08-16 周期的 H2H + 近期状态（实时打印进度）

对每场比赛：
  1) 主客两队：调 SM team latest → 更新 form + recent_matches（season=latest 记录）
  2) 该对位 H2H：调 SM head-to-head → 新增缺失记录（比分+日期+fx，stats 由全量 update_teams 补齐）
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(r"e:\zhangxuejun\new-thinking\ricking-03\backend\.env", override=True)

from sqlalchemy import select
from app.collector.pipeline import SyncPipeline
from app.db.database import async_session
from app.db.models import Match, Team, TeamSeasonStats, HeadToHead

OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_per_match.txt"
_log = []


def p(s=""):
    print(s, flush=True)
    _log.append(str(s))


async def _update_team_recent(pipeline, db, team: Team) -> str:
    """更新单支球队的 form + recent_matches（仅当有 SM id 且数据过时/为空）"""
    if not team.sportmonks_id:
        return "无SM"
    try:
        data = await pipeline.sm.get_team_by_id(
            team.sportmonks_id, includes="latest;latest.participants;latest.scores"
        )
    except Exception as e:
        return f"SM失败:{e}"
    latest = data.get("latest", [])
    if not latest:
        return "SM无latest"
    form = pipeline._derive_form_from_matches(latest, team.sportmonks_id)
    recent = pipeline._extract_recent_matches(latest, team.sportmonks_id)
    latest_date = ""
    for m in recent:
        if m.get("date"):
            latest_date = m["date"]
    if not form and not recent:
        return "无数据"
    # 找到或创建最新 season 记录（与 sync_team_info 阶段2 一致）
    res = await db.execute(
        select(TeamSeasonStats).where(TeamSeasonStats.team_id == team.id)
        .order_by(TeamSeasonStats.season.desc()).limit(1)
    )
    target = res.scalar_one_or_none()
    if not target:
        target = TeamSeasonStats(team_id=team.id, season="latest")
        db.add(target)
    if form and not target.form:
        target.form = form
    if recent:
        target.recent_matches = recent
    await db.flush()
    return f"更新 {len(recent)} 场/最新{latest_date}"


async def _sync_h2h_for_pair(pipeline, db, t1: int, t2: int) -> str:
    """同步一对球队的 H2H（新增缺失的比分记录，不拉 fixture 统计）"""
    res1 = await db.execute(select(Team.sportmonks_id).where(Team.id == t1))
    res2 = await db.execute(select(Team.sportmonks_id).where(Team.id == t2))
    sm1, sm2 = res1.scalar(), res2.scalar()
    if not sm1 or not sm2:
        return f"sm缺失({sm1},{sm2})"
    try:
        h2h_data = await pipeline.sm.get_head_to_head(sm1, sm2)
    except Exception as e:
        return f"SM失败:{e}"
    if not isinstance(h2h_data, list):
        return "SM异常"
    if not h2h_data:
        return "SM无H2H"
    added = 0
    for h in h2h_data[:6]:
        fid = h.get("id")
        # 已存在则跳过
        if fid:
            dup = await db.execute(select(HeadToHead).where(HeadToHead.sportmonks_fixture_id == fid))
            if dup.scalar_one_or_none():
                continue
        match_date_str = h.get("starting_at", "")
        try:
            match_date = datetime.strptime(match_date_str[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            match_date = datetime.utcnow()
        participants = h.get("participants", [])
        if len(participants) < 2:
            continue
        # 按 meta.location 识别主队
        local_sm = None
        for par in participants:
            meta = par.get("meta") or {}
            if meta.get("location") == "home":
                local_sm = par.get("id")
                break
        if not local_sm and participants:
            local_sm = participants[0].get("id")
        # 比分
        home_score = away_score = None
        for s in (h.get("scores") or []):
            if not isinstance(s, dict) or s.get("description") != "CURRENT":
                continue
            goals = (s.get("score") or {}).get("goals")
            pid = s.get("participant_id")
            if pid == local_sm:
                home_score = goals
            else:
                away_score = goals
        if local_sm == sm1:
            ht_id, at_id = t1, t2
        else:
            ht_id, at_id = t2, t1
        db.add(HeadToHead(
            home_team_id=ht_id,
            away_team_id=at_id,
            match_date=match_date,
            competition=(h.get("league") or {}).get("name", "") if isinstance(h.get("league"), dict) else "",
            home_score=home_score,
            away_score=away_score,
            sportmonks_fixture_id=fid,
            home_stats=None,
            away_stats=None,
        ))
        added += 1
    await db.flush()
    return f"SM {len(h2h_data)} 条, 新增 {added}"


async def main():
    pipeline = SyncPipeline()
    try:
        async with async_session() as db:
            # 08-15 周期: 2026-08-15 12:00 ~ 2026-08-16 12:00
            # 08-16 周期: 2026-08-16 12:00 ~ 2026-08-17 12:00
            windows = [
                ("08-15 周期", datetime(2026, 8, 15, 12, 0), datetime(2026, 8, 16, 12, 0)),
                ("08-16 周期", datetime(2026, 8, 16, 12, 0), datetime(2026, 8, 17, 12, 0)),
            ]
            for label, d0, d1 in windows:
                p(f"\n{'='*60}\n{label} ({d0:%m-%d %H:%M} ~ {d1:%m-%d %H:%M})\n{'='*60}")
                res = await db.execute(
                    select(Match).where(
                        Match.kickoff_time >= d0,
                        Match.kickoff_time < d1,
                        Match.home_team_id.isnot(None),
                        Match.away_team_id.isnot(None),
                    ).order_by(Match.kickoff_time)
                )
                matches = res.scalars().all()
                p(f"共 {len(matches)} 场\n")
                for i, m in enumerate(matches, 1):
                    h = await db.get(Team, m.home_team_id)
                    a = await db.get(Team, m.away_team_id)
                    h_name = h.name_zh if h else f"#{m.home_team_id}"
                    a_name = a.name_zh if a else f"#{m.away_team_id}"
                    p(f"── [{i}/{len(matches)}] #{m.id} {m.kickoff_time:%m-%d %H:%M} {h_name} vs {a_name} ──")
                    # 两队近期状态（每队去重：同一队只更新一次）
                    for tid, side in [(m.home_team_id, "主"), (m.away_team_id, "客")]:
                        team = await db.get(Team, tid)
                        if not team:
                            p(f"  {side}: team#{tid} 不存在")
                            continue
                        # 已有较新数据则跳过（recent_matches 非空且最新日期 >= 昨日）
                        skip = False
                        st = await db.execute(
                            select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid)
                            .order_by(TeamSeasonStats.season.desc()).limit(1)
                        )
                        s = st.scalar_one_or_none()
                        if s and s.recent_matches:
                            rm = s.recent_matches
                            if isinstance(rm, str):
                                import json
                                try:
                                    rm = json.loads(rm)
                                except Exception:
                                    rm = []
                            if rm and rm[0].get("date", "") >= (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d"):
                                skip = True
                        if skip:
                            p(f"  {side} [{team.name_zh}] 已有近期数据，跳过")
                            continue
                        msg = await _update_team_recent(pipeline, db, team)
                        p(f"  {side} [{team.name_zh}(sm={team.sportmonks_id})] {msg}")
                    # 该对位 H2H
                    msg = await _sync_h2h_for_pair(pipeline, db, m.home_team_id, m.away_team_id)
                    p(f"  H2H: {msg}")
                    await db.commit()
    finally:
        try:
            await pipeline.sm.close()
        except Exception:
            pass
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(_log))
    p(f"\n[written] {OUT}")


asyncio.run(main())
