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


def _norm_key(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _score_candidate(cand: dict, term: str) -> int:
    """SportMonks 候选可信度打分（务必防止缩写子串误命中）。

    100=缩写精确 | 95=名称精确 | 80=名称前缀；<80 视为不可信。
    例：搜索 'DER' 时 SunDERland 只在词中出现 → 0 分被拒；Derby County 名称前缀命中 → 80 分。
    """
    t = _norm_key(term)
    if not t:
        return 0
    short = _norm_key(cand.get("short_code") or "")
    name = _norm_key(cand.get("name") or "")
    if short and short == t:
        return 100
    if name and name == t:
        return 95
    if name.startswith(t):
        return 80
    return 0


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
    # 2026-08-07 新增：荷甲/荷乙/德乙/葡超/英冠/日职联
    "坎布尔": "Cambuur", "柏林赫塔": "Hertha BSC",
    "法马利康": "Famalicao", "女王公园巡游者": "Queens Park Rangers",
    "前进之鹰": "Go Ahead Eagles", "威廉二世": "Willem II",
    "吉马良斯": "Vitoria Guimaraes", "阿尔克马尔": "AZ Alkmaar",
    "山形山神": "Montedio Yamagata", "菲尔特": "Greuther Furth",
    "兹沃勒": "PEC Zwolle", "阿贾克斯": "Ajax",
    "吉维森特": "Gil Vicente",
    # 2026-08-08 新增：修复历史数据中name_en错误的球队
    "波尔图": "Porto", "阿尔维卡": "Alverca", "圣保利": "St Pauli",
    "德累斯顿": "Dresden", "海牙": "ADO Den Haag",
    "波城FC": "Pau", "阿纳西": "Annecy", "南特": "Nantes",
    "圣旺红星": "Red Star", "蒙彼利埃": "Montpellier",
    "第戎": "Dijon", "阿罗卡": "Arouca", "马里迪莫": "Maritimo",
    "卡萨皮亚": "Casa Pia", "西汉姆联": "West Ham",
    "埃斯托里尔": "Estoril", "SBV精英": "Excelsior",
    "新潟天鹅": "Albirex Niigata", "拉赫蒂": "Lahti",
    "米拉索尔": "Mirassol", "格雷米奥": "Gremio",
    "桑托斯": "Santos", "里莫": "Remo", "维多利亚": "Vitoria",
    "博德闪耀": "Bodo Glimt", "汉坎": "HamKam",
    "哈马比": "Hammarby", "斯达": "Start",
    "瓦萨": "VPS", "库普斯": "KuPS",
    "塞伊奈约基": "SJK", "朴次茅斯": "Portsmouth",
    "斯旺西": "Swansea", "伯明翰": "Birmingham",
    "米尔沃尔": "Millwall", "雷克斯汉姆": "Wrexham",
    "米德尔斯堡": "Middlesbrough",
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

    async def _reverse_lookup(_team):
        """反向匹配：通过该队赛事中【已映射】的对手，在 SM 赛程里反查另一名参赛者。
        同时覆盖已关联 team_id 的赛事与仅存原始队名的历史赛事。
        返回 (sm_id, name, short_code, search_term) 或 None。"""
        nonlocal api_errors
        from datetime import timedelta
        zh = _team.name_zh or ""
        conds = [Match.home_team_id == _team.id, Match.away_team_id == _team.id]
        if zh:
            conds += [Match.home_team_name == zh, Match.away_team_name == zh]
        rows = (await db.execute(
            select(Match).where(Match.kickoff_time.isnot(None), or_(*conds))
            .order_by(Match.kickoff_time.desc()).limit(5))).scalars().all()
        for m in rows:
            team_is_home = (m.home_team_id == _team.id) or (m.home_team_name == zh)
            opp_name = m.away_team_name if team_is_home else m.home_team_name
            opp_id = m.away_team_id if team_is_home else m.home_team_id
            opp = None
            if opp_id:
                opp = (await db.execute(select(Team).where(Team.id == opp_id))).scalar_one_or_none()
            if (not opp or not opp.sportmonks_id) and opp_name:
                a = (await db.execute(select(TeamAlias).where(
                    TeamAlias.alias_name == opp_name).limit(1))).scalar_one_or_none()
                if a:
                    opp = (await db.execute(select(Team).where(Team.id == a.team_id))).scalar_one_or_none()
            if not opp or not opp.sportmonks_id:
                continue
            date_from = (m.kickoff_time - timedelta(days=2)).strftime("%Y-%m-%d")
            date_to = (m.kickoff_time + timedelta(days=2)).strftime("%Y-%m-%d")
            try:
                fixtures = await sm.get_fixtures_between(date_from, date_to, includes="participants")
            except Exception as e:
                api_errors += 1
                if api_errors <= 3:
                    print(f"[batch-match] L5 error for '{zh}': {e}", flush=True)
                continue
            for fx in fixtures:
                ps = fx.get("participants") or []
                if len(ps) < 2:
                    continue
                ids = [p.get("id") for p in ps if isinstance(p, dict)]
                if opp.sportmonks_id not in ids:
                    continue
                other = next((p for p in ps if isinstance(p, dict)
                              and p.get("id") and p.get("id") != opp.sportmonks_id), None)
                if not other:
                    continue
                return (other.get("id"), other.get("name", ""), other.get("short_code", ""),
                        f"reverse:{opp.name_zh or opp.name_en}(sm{opp.sportmonks_id})")
        return None

    for team in teams:
        tr = {"id": team.id, "name_zh": team.name_zh or "", "name_en": team.name_en or "",
              "status": "unmatched", "sm_id": None, "sm_name": None, "sm_short": None,
              "search_term": None, "layer": None}

        if team.sportmonks_id:  # Layer 1
            continue

        # Layer 5（优先执行）：反向匹配最可靠，避免缩写被 SportMonks 模糊命中错误球队
        rev = await _reverse_lookup(team)
        if rev:
            sm_id, sm_name, sm_short, term = rev
            dup = (await db.execute(select(Team).where(
                Team.sportmonks_id == sm_id, Team.id != team.id))).scalar_one_or_none()
            if dup:
                await _do_merge_team(sm_id, sm_name, sm_short, term, "L5", dup)
            else:
                await _do_match_team(sm_id, sm_name, sm_short, term, "L5",
                                     note=f"通过对手反向匹配: {term}")
            results.append(tr)
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
        candidates = []

        for term in all_terms[:6]:
            try:
                sm_results = await sm.search_teams(term)
            except Exception as e:
                api_errors += 1
                if api_errors <= 3: print(f"[batch-match] SM error '{term}': {e}", flush=True)
                continue
            if not sm_results:
                continue
            scored = sorted(
                ((_score_candidate(c, term), c) for c in sm_results if isinstance(c, dict)),
                key=lambda p: -p[0])
            for _, cand in scored[:3]:
                if cand.get("name"):
                    candidates.append(f"{cand.get('name')}[{cand.get('short_code') or ''}]")
            score, best = scored[0] if scored else (0, None)
            # 提取的拉丁片段可信度低，要求更强命中；其余也需 ≥80 分，杜绝子串误命中
            min_score = 95 if term in l3 else 80
            if not best or score < min_score:
                continue
            sm_id = best.get("id")
            if not sm_id:
                continue
            sm_name = best.get("name", "")
            sm_short = best.get("short_code", "")
            dup = await db.execute(select(Team).where(Team.sportmonks_id == sm_id, Team.id != team.id))
            existing = dup.scalar_one_or_none()
            layer = "L2" if term in l1 else ("L3" if term in l2 else "L4")
            if existing:
                await _do_merge_team(sm_id, sm_name, sm_short, term, layer, existing)
            else:
                if term in l3 and team.name_zh:
                    CN_TO_EN_SEARCH[team.name_zh] = term  # 自动扩充
                await _do_match_team(sm_id, sm_name, sm_short, term, layer)
            break

        if not found:
            uniq = list(dict.fromkeys(candidates))[:5]
            tr.update(search_term=all_terms[0] if all_terms else None,
                      note=f"未找到可信匹配；候选词 {all_terms[:3]}；SM 候选 {uniq}")
        results.append(tr)

    await db.commit()
    return {"data": {"total": len(teams), "matched": matched_count, "api_errors": api_errors, "results": results}}
