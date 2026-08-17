"""逐场同步 08-15/08-16 周期 H2H + 近期状态（按比赛逐个处理并打印进度）
- 对每场比赛：
  1. 检查 H2H 是否有记录（无 → 调 SM get_head_to_head 补录基础记录）
  2. 检查双方球队顶级 stats 记录（season DESC LIMIT 1，UI 实际读取）recent_matches 是否为空
     （空 → 调 SM get_team_by_id(latest) 补 recent/form）
  3. 每场结束打印该场进度
"""
import asyncio
import os
import sys
import json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv(r"e:\zhangxuejun\new-thinking\ricking-03\backend\.env", override=True)

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, Team, TeamSeasonStats, HeadToHead
from app.collector.pipeline import SyncPipeline

OUT = "e:/zhangxuejun/new-thinking/ricking-03/backend/tools/_out_per_match_sync.txt"
_log = []


def p(s=""):
    print(s, flush=True)
    _log.append(str(s))


def _recent_count(rm):
    if not rm:
        return 0
    if isinstance(rm, str):
        try:
            return len(json.loads(rm))
        except Exception:
            return 0
    return len(rm) if isinstance(rm, list) else 0


async def sync_team_recent(pipeline, db, team: Team):
    """补齐球队顶级 stats 记录的 recent_matches/form（SM latest 数据）"""
    if not team.sportmonks_id:
        return "无sm_id"
    top = (await db.execute(
        select(TeamSeasonStats).where(TeamSeasonStats.team_id == team.id)
        .order_by(TeamSeasonStats.season.desc()).limit(1)
    )).scalar_one_or_none()
    if top and _recent_count(top.recent_matches) > 0:
        return "已有数据"
    try:
        team_data = await pipeline.sm.get_team_by_id(
            team.sportmonks_id, includes="sidelined;statistics;latest;latest.participants;latest.scores"
        )
    except Exception as e:
        return f"SM拉取失败:{type(e).__name__}"
    latest_matches = team_data.get("latest", [])
    form_chars = pipeline._derive_form_from_matches(latest_matches, team.sportmonks_id)
    recent = pipeline._extract_recent_matches(latest_matches, team.sportmonks_id)
    if not form_chars and not recent:
        return "SM无最新数据"
    if not top:
        top = TeamSeasonStats(team_id=team.id, season="latest")
        db.add(top)
        await db.flush()
    if form_chars and not top.form:
        top.form = form_chars
    if recent:
        top.recent_matches = recent
    return f"已补recent={len(recent)}条 form={form_chars or top.form or '-'}"


async def sync_h2h_pair(pipeline, db, home_team_id, away_team_id, sm1, sm2):
    """补齐一对球队的 H2H 基础记录"""
    exist = (await db.execute(
        select(HeadToHead).where(
            ((HeadToHead.home_team_id == home_team_id) & (HeadToHead.away_team_id == away_team_id)) |
            ((HeadToHead.home_team_id == away_team_id) & (HeadToHead.away_team_id == home_team_id))
        )
    )).scalars().all()
    if exist:
        return f"已有{len(exist)}条"
    if not sm1 or not sm2:
        return "sm缺失"
    try:
        data = await pipeline.sm.get_head_to_head(sm1, sm2)
    except Exception as e:
        return f"SM拉取失败:{type(e).__name__}"
    if not isinstance(data, list) or len(data) == 0:
        return "SM无H2H数据"
    added = 0
    for h in data[:6]:
        fx = h.get("id")
        dup = (await db.execute(
            select(HeadToHead).where(HeadToHead.sportmonks_fixture_id == fx)
        )).scalar_one_or_none() if fx else None
        if dup:
            continue
        try:
            match_date = datetime.strptime(str(h.get("starting_at", ""))[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            match_date = datetime.utcnow()
        participants = h.get("participants", []) or []
        local_team_sm = None
        for pp in participants:
            if isinstance(pp, dict) and ((pp.get("meta") or {}).get("location") == "home"):
                local_team_sm = pp.get("id")
                break
        if not local_team_sm and participants:
            local_team_sm = participants[0].get("id")
        scores = h.get("scores", []) or []
        hs = aws = None
        for s in scores:
            if isinstance(s, dict) and s.get("description") == "CURRENT":
                pid = s.get("participant_id")
                g = (s.get("score") or {}).get("goals")
                if pid == local_team_sm:
                    hs = g
                else:
                    aws = g
        if local_team_sm == sm1:
            ht_id, at_id = home_team_id, away_team_id
        else:
            ht_id, at_id = away_team_id, home_team_id
        db.add(HeadToHead(
            home_team_id=ht_id,
            away_team_id=at_id,
            match_date=match_date,
            competition=(h.get("league") or {}).get("name", "") if isinstance(h.get("league"), dict) else "",
            home_score=int(hs) if hs is not None else None,
            away_score=int(aws) if aws is not None else None,
            sportmonks_fixture_id=fx,
        ))
        added += 1
    await db.flush()
    return f"新增{added}条(SM共{len(data)}条)"


async def main():
    pipeline = SyncPipeline()
    day_rows = []
    for day in ["2026-08-15", "2026-08-16"]:
        start = datetime.strptime(f"{day} 12:00:00", "%Y-%m-%d %H:%M:%S")
        end = start + timedelta(hours=24)
        day_rows.append((day, start, end))

    total = 0
    for day, start, end in day_rows:
        async with async_session() as db:
            rows = (await db.execute(
                select(Match).where(Match.kickoff_time >= start, Match.kickoff_time < end)
                .order_by(Match.kickoff_time)
            )).scalars().all()
            total += len(rows)

    idx = 0
    for day, start, end in day_rows:
        p(f"\n{'='*88}")
        p(f"=== {day} 周期（{day} 12:00 ~ 次日 12:00）===")
        p(f"{'='*88}")
        async with async_session() as db:
            rows = (await db.execute(
                select(Match).where(Match.kickoff_time >= start, Match.kickoff_time < end)
                .order_by(Match.kickoff_time)
            )).scalars().all()
            for m in rows:
                idx += 1
                home = (await db.execute(select(Team).where(Team.id == m.home_team_id))).scalar_one_or_none()
                away = (await db.execute(select(Team).where(Team.id == m.away_team_id))).scalar_one_or_none()
                hn = home.name_zh if home else f"id={m.home_team_id}"
                an = away.name_zh if away else f"id={m.away_team_id}"
                p(f"\n--- [{idx}/{total}] #{m.id} {hn} vs {an} ---")
                # H2H
                h2h_msg = await sync_h2h_pair(
                    pipeline, db, m.home_team_id, m.away_team_id,
                    home.sportmonks_id if home else None,
                    away.sportmonks_id if away else None,
                )
                p(f"  H2H: {h2h_msg}")
                # 球队近期状态
                if home:
                    msg = await sync_team_recent(pipeline, db, home)
                    p(f"  主队[{hn} sm={home.sportmonks_id}]: {msg}")
                if away:
                    msg = await sync_team_recent(pipeline, db, away)
                    p(f"  客队[{an} sm={away.sportmonks_id}]: {msg}")
                await db.commit()

    await pipeline.sm.close()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(_log))
    p(f"\n[written] {OUT}")


asyncio.run(main())
