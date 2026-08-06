"""名称映射管理 API"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from app.db.database import get_db
from app.db.models import Team, TeamAlias, League, LeagueAlias, Match

router = APIRouter(prefix="/api/mappings", tags=["mappings"])


def _has_cjk(s: str) -> bool:
    for ch in s:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
            or 0x3040 <= cp <= 0x30FF or 0xAC00 <= cp <= 0xD7AF):
            return True
    return False


def _extract_latin(name: str) -> str:
    """从中文名中提取拉丁字符（如 '奥斯KFUM' → 'KFUM', '圣迭戈FC' → 'FC'）"""
    latin = [ch for ch in name if ord(ch) < 128 and ch.isalpha()]
    result = "".join(latin)
    return result if len(result) >= 2 else ""


# 竞彩网中文名 → SportMonks 英文搜索词映射（SportMonks 不支持中文搜索）
# batch-match 第4层命中时会自动扩充此表
CN_TO_EN_SEARCH = {
    "埃夫斯堡": "Elfsborg", "TPS图尔": "TPS", "弗鲁米嫩": "Fluminense",
    "圣迭戈FC": "San Diego", "达拉斯": "Dallas", "圣何塞": "San Jose",
    "洛城银河": "LA Galaxy", "国际图尔": "Inter Turku", "盖斯": "GAIS",
    "哈尔姆斯": "Halmstad", "巴竞技": "Athletico Paranaense",
    "克里斯蒂": "Kristiansund", "奥斯KFUM": "KFUM Oslo",
    "萨普斯堡": "Sarpsborg", "桑纳菲": "Sandefjord",
    "坦佩雷山猫": "Tampere", "布鲁马波": "Brommapojkarna",
    "赫尔辛基火花": "HIFK",  # 赫尔火花 / HIFK，不是 HJK 赫尔辛基
    "赫尔辛基": "HJK",
}


@router.get("/stats")
async def get_mapping_stats(type: str = Query("team"), db: AsyncSession = Depends(get_db)):
    if type == "league":
        total_result = await db.execute(select(func.count(League.id)))
        total = total_result.scalar() or 0
        matched_result = await db.execute(
            select(func.count(func.distinct(LeagueAlias.league_id))).where(LeagueAlias.source == "sportmonks"))
        matched = matched_result.scalar() or 0
        pending = total - matched
    else:
        total_result = await db.execute(select(func.count(Team.id)))
        total = total_result.scalar() or 0
        pending_result = await db.execute(
            select(func.count(Team.id)).where((Team.name_zh == None) | (Team.needs_review == True)))
        pending = pending_result.scalar() or 0
        matched = total - pending
    return {"data": {"total": total, "auto_matched": matched, "pending": pending}}


@router.get("/leagues")
async def get_league_mappings(search: str = Query(None), status: str = Query(None), db: AsyncSession = Depends(get_db)):
    query = select(League)
    if status == "pending":
        query = query.where(
            ~League.id.in_(select(LeagueAlias.league_id).where(LeagueAlias.source == "sportmonks")))
    elif status == "confirmed":
        query = query.where(
            League.id.in_(select(LeagueAlias.league_id).where(LeagueAlias.source == "sportmonks")))
    if search:
        like = f"%{search}%"
        query = query.where(League.name_zh.like(like) | League.name_en.like(like)
            | League.id.in_(select(LeagueAlias.league_id).where(LeagueAlias.alias_name.like(like))))
    query = query.order_by(League.id)
    result = await db.execute(query)
    leagues = result.scalars().all()
    data = []
    for league in leagues:
        ar = await db.execute(select(LeagueAlias).where(LeagueAlias.league_id == league.id))
        aliases = ar.scalars().all()
        data.append({"id": league.id, "name_zh": league.name_zh, "name_en": league.name_en,
            "sportmonks_id": league.sportmonks_id,
            "aliases": [{"name": a.alias_name, "source": a.source, "is_primary": a.is_primary} for a in aliases],
            "status": "confirmed" if any(a.source == "sportmonks" for a in aliases) else "pending"})
    return {"data": data}


@router.get("/teams")
async def get_team_mappings(league_id: int = Query(None), search: str = Query(None), status: str = Query(None), db: AsyncSession = Depends(get_db)):
    query = select(Team)
    if status == "pending":
        query = query.where((Team.name_zh == None) | (Team.needs_review == True))
    elif status == "confirmed":
        query = query.where(Team.name_zh != None, Team.needs_review == False)
    if league_id:
        query = query.where(Team.league_id == league_id)
    if search:
        like = f"%{search}%"
        query = query.where(Team.name_zh.like(like) | Team.name_en.like(like)
            | Team.id.in_(select(TeamAlias.team_id).where(TeamAlias.alias_name.like(like)))
            | Team.league_id.in_(select(League.id).where(League.name_zh.like(like))))
    query = query.order_by(Team.id)
    result = await db.execute(query)
    teams = result.scalars().all()
    data = []
    for team in teams:
        ar = await db.execute(select(TeamAlias).where(TeamAlias.team_id == team.id))
        aliases = ar.scalars().all()
        lr = await db.execute(select(League.name_zh).where(League.id == team.league_id))
        league_name = lr.scalar() or ""
        data.append({"id": team.id, "sportmonks_id": team.sportmonks_id,
            "league_name": league_name, "name_zh": team.name_zh, "name_en": team.name_en,
            "short_en": team.short_en,
            "aliases": [{"name": a.alias_name, "source": a.source, "is_primary": a.is_primary} for a in aliases],
            "status": "pending" if (not team.name_zh or team.needs_review) else "confirmed",
            "review_reason": team.review_reason or ""})
    return {"data": data}


@router.get("/pending")
async def get_pending_mappings(type: str = Query("team"), db: AsyncSession = Depends(get_db)):
    if type == "league":
        result = await db.execute(select(League).where(
            ~League.id.in_(select(LeagueAlias.league_id).where(LeagueAlias.source == "sportmonks"))))
        items = result.scalars().all()
        return {"data": [{"id": l.id, "name_zh": l.name_zh, "name_en": l.name_en} for l in items]}
    result = await db.execute(
        select(Team).where((Team.name_zh == None) | (Team.needs_review == True)).order_by(Team.id))
    teams = result.scalars().all()
    return {"data": [{"id": t.id, "sportmonks_id": t.sportmonks_id,
        "name_en": t.name_en, "name_zh": t.name_zh or "",
        "needs_review": t.needs_review, "review_reason": t.review_reason or ""} for t in teams]}


@router.post("/confirm")
async def confirm_mapping(payload: dict, db: AsyncSession = Depends(get_db)):
    type_ = payload.get("type", "team")
    entity_id = payload["id"]
    if type_ == "league":
        result = await db.execute(select(League).where(League.id == entity_id))
        entity = result.scalar_one_or_none()
        if not entity: return {"status": "error", "message": "联赛不存在"}
        if "name_en" in payload: entity.name_en = payload["name_en"]
        if "sportmonks_id" in payload: entity.sportmonks_id = payload["sportmonks_id"]
    else:
        result = await db.execute(select(Team).where(Team.id == entity_id))
        entity = result.scalar_one_or_none()
        if not entity: return {"status": "error", "message": "球队不存在"}
        if "name_zh" in payload: entity.name_zh = payload["name_zh"]
        if "name_en" in payload: entity.name_en = payload["name_en"]
        if "sportmonks_id" in payload: entity.sportmonks_id = payload["sportmonks_id"]
        entity.needs_review = False
        entity.review_reason = None
    await db.commit()
    return {"status": "ok"}


@router.post("/add-alias")
async def add_alias(payload: dict, db: AsyncSession = Depends(get_db)):
    type_ = payload.get("type", "team")
    entity_id = payload["id"]
    alias_name = payload["alias_name"]
    source = payload.get("source", "manual")
    model_cls = TeamAlias if type_ == "team" else LeagueAlias
    id_col = "team_id" if type_ == "team" else "league_id"
    alias = model_cls(**{id_col: entity_id}, alias_name=alias_name, source=source)
    db.add(alias)
    await db.commit()
    return {"status": "ok"}


@router.post("/batch-match")
async def batch_match_teams(db: AsyncSession = Depends(get_db)):
    """
    批量匹配：6层策略
      1. 已有 SM ID → 跳过
      2. 非 UNKNOWN 英文名/别名 → SM 搜索
      3. CN_TO_EN_SEARCH 查表 → SM 搜索
      4. 自动提取拉丁字符 → SM 搜索，命中后自动扩充缓存
      5. 反向匹配：通过已匹配对手在 SM 赛事中反向查找
      6. 以上均失败 → 标记待确认
    """
    from app.collector.sportmonks.client import SportMonksClient

    async def _do_match_team(sm_id, sm_name, sm_short, search_term, layer, note=None):
        """执行球队匹配：设置 SM 字段、添加别名、标记状态"""
        nonlocal matched_count, found
        team.sportmonks_id = sm_id
        team.name_en = sm_name if sm_name else team.name_en
        team.short_en = sm_short or ""
        team.needs_review = False
        team.review_reason = None
        for an, src in [(sm_name, "sportmonks") if sm_name else None,
                        (team.name_zh, "sporttery.cn") if team.name_zh else None]:
            if not an: continue
            ex = await db.execute(select(TeamAlias).where(
                and_(TeamAlias.team_id == team.id, TeamAlias.alias_name == an)))
            if not ex.scalar_one_or_none():
                db.add(TeamAlias(team_id=team.id, alias_name=an, source=src, is_primary=(src == "sporttery.cn")))
        tr.update(status="matched", sm_id=sm_id, sm_name=sm_name, sm_short=sm_short or "",
                  search_term=search_term, layer=layer)
        if note:
            tr["note"] = note
        matched_count += 1; found = True

    async def _do_merge_team(sm_id, sm_name, sm_short, search_term, layer, existing):
        """执行球队合并：覆盖已有记录、迁移赛事、删除占位记录"""
        nonlocal matched_count, found
        old_zh = existing.name_zh
        if team.name_zh:
            existing.name_zh = team.name_zh
            existing.name_en = sm_name if sm_name else existing.name_en
            existing.short_en = sm_short or existing.short_en
            existing.needs_review = False
            existing.review_reason = None
        if team.name_zh:
            ex_alias = await db.execute(
                select(TeamAlias).where(and_(TeamAlias.team_id == existing.id, TeamAlias.alias_name == team.name_zh)))
            if not ex_alias.scalar_one_or_none():
                db.add(TeamAlias(team_id=existing.id, alias_name=team.name_zh, source="sporttery.cn", is_primary=True))
        match_result = await db.execute(
            select(Match).where((Match.home_team_id == team.id) | (Match.away_team_id == team.id)))
        migrated = 0
        for m in match_result.scalars().all():
            if m.home_team_id == team.id: m.home_team_id = existing.id
            if m.away_team_id == team.id: m.away_team_id = existing.id
            migrated += 1
        pa = await db.execute(select(TeamAlias).where(TeamAlias.team_id == team.id))
        for a in pa.scalars().all(): await db.delete(a)
        await db.flush()
        await db.delete(team)
        tr.update(status="merged", sm_id=sm_id, sm_name=sm_name, sm_short=sm_short or "",
                  search_term=search_term, layer=layer,
                  note=f"合并到现有记录 id={existing.id}（旧名: {old_zh} → 新名: {team.name_zh}），迁移 {migrated} 场赛事")
        matched_count += 1; found = True

    result = await db.execute(
        select(Team).where((Team.name_zh == None) | (Team.needs_review == True)).order_by(Team.id))
    teams = result.scalars().all()
    if not teams:
        return {"data": {"total": 0, "matched": 0, "api_errors": 0, "results": []}}

    sm = SportMonksClient()
    results, matched_count, api_errors = [], 0, 0

    for team in teams:
        tr = {"id": team.id, "name_zh": team.name_zh or "", "name_en": team.name_en or "",
              "status": "unmatched", "sm_id": None, "sm_name": None, "sm_short": None,
              "search_term": None, "layer": None}

        if team.sportmonks_id:  # Layer 1
            continue

        l1, l2, l3 = [], [], []

        # Layer 2: 英文名 + 别名
        if team.name_en and not team.name_en.startswith("UNKNOWN:") and not _has_cjk(team.name_en):
            l1.append(team.name_en)
        ar = await db.execute(select(TeamAlias).where(TeamAlias.team_id == team.id))
        for a in ar.scalars().all():
            if not _has_cjk(a.alias_name) and a.alias_name not in l1:
                l1.append(a.alias_name)

        # Layer 3: 映射表
        if team.name_zh:
            en = CN_TO_EN_SEARCH.get(team.name_zh)
            if en: l2.append(en)

        # Layer 4: 提取拉丁字符
        if team.name_zh:
            latin = _extract_latin(team.name_zh)
            if latin and latin not in l1 and latin not in l2:
                l3.append(latin)

        all_terms = l1 + l2 + l3
        found = False

        for term in all_terms[:6]:
            try:
                sm_results = await sm.search_teams(term)
                if sm_results:
                    best = sm_results[0] if isinstance(sm_results[0], dict) else sm_results[0]
                    sm_id = best.get("id")
                    if sm_id:
                        sm_name = best.get("name", "")
                        sm_short = best.get("short_code", "")
                        dup = await db.execute(select(Team).where(Team.sportmonks_id == sm_id, Team.id != team.id))
                        existing = dup.scalar_one_or_none()
                        if existing:
                            layer = "L2" if term in l1 else ("L3" if term in l2 else "L4")
                            await _do_merge_team(sm_id, sm_name, sm_short, term, layer, existing)
                            break

                        if term in l3 and team.name_zh:
                            CN_TO_EN_SEARCH[team.name_zh] = term  # 自动扩充

                        layer = "L2" if term in l1 else ("L3" if term in l2 else "L4")
                        await _do_match_team(sm_id, sm_name, sm_short, term, layer)
                        break
            except Exception as e:
                api_errors += 1
                if api_errors <= 3: print(f"[batch-match] SM error '{term}': {e}", flush=True)
                continue

        if not found and tr.get("status") == "unmatched":
            # Layer 5: 反向匹配 —— 通过已匹配对手在 SM 赛事中反向查找
            match_query = await db.execute(
                select(Match).where(
                    or_(Match.home_team_id == team.id, Match.away_team_id == team.id),
                    Match.kickoff_time.isnot(None),
                ).order_by(Match.kickoff_time.desc()).limit(5)
            )
            l5_matches = match_query.scalars().all()
            for m in l5_matches:
                opponent_id = m.away_team_id if m.home_team_id == team.id else m.home_team_id
                opp_result = await db.execute(select(Team).where(Team.id == opponent_id))
                opponent = opp_result.scalar_one_or_none()
                if not opponent or not opponent.sportmonks_id:
                    continue
                try:
                    from datetime import timedelta
                    # 前后各2天窗口（应对时区差异），用 between 一次拉取
                    date_from = (m.kickoff_time - timedelta(days=2)).strftime("%Y-%m-%d")
                    date_to = (m.kickoff_time + timedelta(days=2)).strftime("%Y-%m-%d")
                    fixtures = await sm.get_fixtures_between(date_from, date_to, includes="participants")
                    for fx in fixtures:
                        fx_participants = fx.get("participants", [])
                        if len(fx_participants) < 2:
                            continue
                        for i, p in enumerate(fx_participants):
                            pid = p.get("id") if isinstance(p, dict) else None
                            if pid and pid == opponent.sportmonks_id:
                                other = fx_participants[1 - i]
                                other_id = other.get("id") if isinstance(other, dict) else None
                                other_name = other.get("name", "") if isinstance(other, dict) else ""
                                if not other_id:
                                    continue
                                dup = await db.execute(select(Team).where(
                                    Team.sportmonks_id == other_id, Team.id != team.id))
                                existing = dup.scalar_one_or_none()
                                search_term = f"reverse:{opponent.name_zh or ''}(sm{opponent.sportmonks_id})"
                                if existing:
                                    await _do_merge_team(other_id, other_name,
                                        other.get("short_code", "") if isinstance(other, dict) else "",
                                        search_term, "L5", existing)
                                else:
                                    await _do_match_team(other_id, other_name,
                                        other.get("short_code", "") if isinstance(other, dict) else "",
                                        search_term, "L5",
                                        note=f"通过对手 {opponent.name_zh or opponent.name_en} 反向匹配")
                                break
                        if found:
                            break
                except Exception as e:
                    api_errors += 1
                    if api_errors <= 3:
                        print(f"[batch-match] L5 reverse error for '{team.name_zh}': {e}", flush=True)
                    continue
                if found:
                    break

            if not found:
                tr.update(note=f"候选词均未匹配: {all_terms[:4]}", search_term=all_terms[0] if all_terms else None)
        results.append(tr)

    await db.commit()
    return {"data": {"total": len(teams), "matched": matched_count, "api_errors": api_errors, "results": results}}
