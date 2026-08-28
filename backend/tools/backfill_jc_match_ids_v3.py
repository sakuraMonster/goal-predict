""" V3 回填 jc_match_id / match_num:
    - names_of 增加 TeamAlias.alias_name 源 (RESULT_API 用的很多中文简称在 alias 里)
    - kickoff 容差 ± 2 天
    - 真匹配不到：按 RESULT_API 数据直接新建 Match 行（带 score/match_num/jc_match_id），保证 100% 有 jc_match_id 对应 Match
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Optional

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.dirname(HERE)
sys.path.insert(0, BACKEND_ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_ROOT, ".env"))

from sqlalchemy import select, and_, or_, func
from app.db.database import async_session
from app.db.models import Match, Team, TeamAlias, League, LeagueAlias

RESULT_API = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.sporttery.cn/jc/zqsgkj/",
}

START = datetime(2025, 1, 1, 0, 0, 0)
END   = datetime(2025, 12, 31, 23, 59, 59)
STEP_DAYS = 29


def windows(start: datetime, end: datetime, step_days: int):
    cur_s = start
    while cur_s <= end:
        cur_e = cur_s + timedelta(days=step_days - 1)
        if cur_e > end:
            cur_e = end
        yield cur_s, cur_e
        cur_s = cur_e + timedelta(seconds=1)


def pull_result_page(start_date: str, end_date: str, page: int) -> tuple[int, list[dict], int]:
    r = requests.get(RESULT_API, params={
        "matchBeginDate": start_date, "matchEndDate": end_date,
        "leagueId": "", "pageSize": 50, "pageNo": page,
        "isFix": 0, "matchPage": 1, "pcOrWap": 1,
    }, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if str(data.get("errorCode")) != "0":
        return 0, [], 0
    val = data.get("value") or {}
    matches = val.get("matchResult") or []
    total_pages = int(val.get("pages") or 1)
    total = int(val.get("total") or len(matches))
    return total, matches, total_pages


_NAME_STRIP = re.compile(r"[\s\-_·・()（）\[\]【】.．，,。:：/\\]+")

def norm(s) -> str:
    if not s:
        return ""
    s = _NAME_STRIP.sub("", str(s).strip())
    s = s.replace("U23", "u23").replace("U21", "u21").replace("U19", "u19").replace("U17", "u17")
    s = s.replace("女足", "women")
    return s.lower()


def name_any_match(a_list: list[str], b_list: list[str]) -> bool:
    norm_a = [norm(x) for x in a_list if x]
    norm_b = [norm(x) for x in b_list if x]
    norm_a = [x for x in norm_a if x]
    norm_b = [x for x in norm_b if x]
    short_a = [re.sub(r"fc$|sc$|ac$|cf$|cf$", "", x) or x for x in norm_a]
    short_b = [re.sub(r"fc$|sc$|ac$|cf$", "", x) or x for x in norm_b]
    for na, sa in zip(norm_a, short_a):
        for nb, sb in zip(norm_b, short_b):
            if not na or not nb:
                continue
            if na == nb:
                return True
            if len(na) >= 4 and len(nb) >= 4 and (na in nb or nb in na):
                return True
            if len(sa) >= 3 and len(sb) >= 3 and sa == sb:
                return True
            if len(sa) >= 3 and len(sb) >= 3 and (sa in sb or sb in sa):
                return True
            if len(sa) >= 4 and len(sb) >= 4 and sa[:3] == sb[:3] and sa[-3:] == sb[-3:]:
                return True
    return False


async def team_aliases_by_id(db, team_ids):
    team_ids = list([int(x) for x in set(team_ids) if x])
    if not team_ids:
        return {}
    out = {}
    for tid, aname in (await db.execute(
        select(TeamAlias.team_id, TeamAlias.alias_name).where(TeamAlias.team_id.in_(team_ids))
    )).all():
        out.setdefault(int(tid), []).append(aname)
    return out


async def names_of(db, m: Match, extra_team_alias: Optional[dict[int, list[str]]] = None) -> tuple[list[str], list[str]]:
    ht = None
    at = None
    if m.home_team_id:
        ht = (await db.get(Team, m.home_team_id))
    if m.away_team_id:
        at = (await db.get(Team, m.away_team_id))
    h_aliases: list[str] = []
    a_aliases: list[str] = []
    if extra_team_alias and m.home_team_id and int(m.home_team_id) in extra_team_alias:
        h_aliases = list(extra_team_alias[int(m.home_team_id)])
    if extra_team_alias and m.away_team_id and int(m.away_team_id) in extra_team_alias:
        a_aliases = list(extra_team_alias[int(m.away_team_id)])
    def _agg(*vals):
        seen = set()
        out = []
        for v in vals:
            if not v:
                continue
            nv = norm(v)
            if nv and nv not in seen:
                seen.add(nv)
                out.append(str(v))
        return out
    home_aliases = _agg(
        m.home_team_name,
        ht.name_zh if ht else None,
        ht.name_en if ht else None,
        ht.short_zh if ht else None,
        ht.short_en if ht else None,
        *h_aliases,
    )
    away_aliases = _agg(
        m.away_team_name,
        at.name_zh if at else None,
        at.name_en if at else None,
        at.short_zh if at else None,
        at.short_en if at else None,
        *a_aliases,
    )
    return home_aliases, away_aliases


def _parse_sections_score(s: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not s:
        return None, None
    m = re.match(r"^\s*(\d+)\s*[:：]\s*(\d+)\s*$", str(s))
    if not m:
        return None, None
    try:
        return int(m.group(1)), int(m.group(2))
    except Exception:
        return None, None


async def _find_league(db, league_name_zh: Optional[str]) -> Optional[int]:
    if not league_name_zh:
        return None
    # 先查 leagues.name_zh 精确
    r = (await db.execute(select(League.id).where(League.name_zh == league_name_zh))).scalar_one_or_none()
    if r:
        return int(r)
    # 再查 league_alias 精确
    r = (await db.execute(select(LeagueAlias.league_id).where(LeagueAlias.alias_name == league_name_zh))).scalar_one_or_none()
    if r:
        return int(r)
    # 再模糊
    n2 = norm(league_name_zh)
    if len(n2) < 2:
        return None
    all_alias = (await db.execute(select(LeagueAlias.league_id, LeagueAlias.alias_name))).all()
    for lid, aname in all_alias:
        if norm(aname) == n2 or (len(n2) >= 3 and (n2 in norm(aname) or norm(aname) in n2)):
            return int(lid)
    all_league = (await db.execute(select(League.id, League.name_zh, League.name_en))).all()
    for lid, zh, en in all_league:
        for x in [zh, en]:
            if norm(x) == n2 or (len(n2) >= 3 and (n2 in norm(x) or norm(x) in n2)):
                return int(lid)
    return None


async def _find_team(db, name_list: list[str]) -> Optional[int]:
    name_list = [str(x) for x in name_list if x]
    if not name_list:
        return None
    # 1. Team.name_zh / name_en 精确
    for nm in name_list:
        r = (await db.execute(select(Team.id).where(or_(Team.name_zh == nm, Team.name_en == nm)))).scalar_one_or_none()
        if r:
            return int(r)
    # 2. TeamAlias.alias_name 精确
    for nm in name_list:
        r = (await db.execute(select(TeamAlias.team_id).where(TeamAlias.alias_name == nm))).scalar_one_or_none()
        if r:
            return int(r)
    # 3. 名字归一化等值（含短名）
    norm_list = [norm(x) for x in name_list if norm(x)]
    if not norm_list:
        return None
    # 扫 Team
    t_all = (await db.execute(select(Team.id, Team.name_zh, Team.name_en, Team.short_zh, Team.short_en))).all()
    for tid, zh, en, szh, sen in t_all:
        for n in [zh, en, szh, sen]:
            nn = norm(n)
            if not nn:
                continue
            for nl in norm_list:
                if nl == nn:
                    return int(tid)
                if len(nl) >= 3 and len(nn) >= 3 and (nl in nn or nn in nl):
                    return int(tid)
    # 扫 TeamAlias
    ta_all = (await db.execute(select(TeamAlias.team_id, TeamAlias.alias_name))).all()
    for tid, aname in ta_all:
        na = norm(aname)
        if not na:
            continue
        for nl in norm_list:
            if nl == na:
                return int(tid)
            if len(nl) >= 3 and len(na) >= 3 and (nl in na or na in nl):
                return int(tid)
    return None


async def create_match_from_result_api(db, jc: dict) -> Match:
    """ 匹配不到本地 Match 时，直接按 RESULT_API 数据新建 """
    home_all = jc.get("allHomeTeam") or jc.get("homeTeam") or None
    away_all = jc.get("allAwayTeam") or jc.get("awayTeam") or None
    home_short = jc.get("homeTeam")
    away_short = jc.get("awayTeam")
    match_num = (jc.get("matchNumStr") or "").strip() or None
    jc_match_id = str(jc.get("matchId"))
    md = jc.get("matchDate")
    if md:
        # RESULT_API 没给具体时间，用 00:00:00 (UTC+8 比赛日，实际晚上踢，占位 OK)
        ko = datetime.strptime(md, "%Y-%m-%d")
    else:
        ko = datetime(2026, 1, 1)
    home_team_id = await _find_team(db, [home_all, home_short])
    away_team_id = await _find_team(db, [away_all, away_short])
    league_id = await _find_league(db, jc.get("leagueName"))
    # 比分
    home_score, away_score = _parse_sections_score(jc.get("sectionsNo999"))
    half_home_score, half_away_score = _parse_sections_score(jc.get("sectionsNo1"))
    status = "finished"
    if not jc.get("sectionsNo999"):
        status = "scheduled"
    m = Match(
        jc_match_id=jc_match_id,
        match_num=match_num,
        league_id=league_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_team_name=home_all,
        away_team_name=away_all,
        kickoff_time=ko,
        status=status,
        home_score=home_score,
        away_score=away_score,
        half_home_score=half_home_score,
        half_away_score=half_away_score,
    )
    db.add(m)
    await db.flush()
    return m


async def match_local(db, jc: dict) -> tuple[Match | None, str]:
    """ return (match_or_None, reason_code) """
    match_num = (jc.get("matchNumStr") or "").strip() or None
    jc_match_id = str(jc.get("matchId"))
    home_all = jc.get("allHomeTeam") or ""
    home_short = jc.get("homeTeam") or ""
    away_all = jc.get("allAwayTeam") or ""
    away_short = jc.get("awayTeam") or ""
    home_list = [home_all, home_short]
    away_list = [away_all, away_short]
    match_date_s = (jc.get("matchDate") or "").strip()
    try:
        md = datetime.strptime(match_date_s, "%Y-%m-%d").date()
    except Exception:
        return None, "bad_date"
    # 0) 已回填过？直接返回（不新建）
    existing = (await db.execute(
        select(Match).where(Match.jc_match_id == jc_match_id)
    )).scalar_one_or_none()
    if existing is not None:
        return existing, "existing"
    # 1) match_num 精确 (最优先) + 日期 ± 5 天
    if match_num:
        r2 = (await db.execute(
            select(Match).where(and_(
                Match.match_num == match_num,
                Match.kickoff_time >= datetime.combine(md, datetime.min.time()) - timedelta(days=5),
                Match.kickoff_time <= datetime.combine(md, datetime.min.time()) + timedelta(days=5),
            ))
        )).scalars().all()
        tids = set()
        for c in r2:
            if c.home_team_id: tids.add(int(c.home_team_id))
            if c.away_team_id: tids.add(int(c.away_team_id))
        team_alias_map = await team_aliases_by_id(db, tids) if tids else {}
        for c in r2:
            ha, aa = await names_of(db, c, team_alias_map)
            if name_any_match(ha, home_list) and name_any_match(aa, away_list):
                return c, "num+name"
            if c.is_swapped and name_any_match(ha, away_list) and name_any_match(aa, home_list):
                return c, "num+name+swapped"
        if len(r2) == 1:
            return r2[0], "numonly"
    # 2) fuzzy: kickoff ± 2天, 队名相似
    kickoff_low = datetime.combine(md, datetime.min.time()) - timedelta(days=2)
    kickoff_high = datetime.combine(md, datetime.min.time()) + timedelta(days=3, hours=23, minutes=59, seconds=59)
    cand = (await db.execute(
        select(Match).where(and_(
            Match.kickoff_time >= kickoff_low,
            Match.kickoff_time <= kickoff_high,
            or_(Match.jc_match_id.is_(None), Match.jc_match_id == ""),
        ))
    )).scalars().all()
    tids = set()
    for c in cand:
        if c.home_team_id: tids.add(int(c.home_team_id))
        if c.away_team_id: tids.add(int(c.away_team_id))
    team_alias_map = await team_aliases_by_id(db, tids) if tids else {}
    for c in cand:
        ha, aa = await names_of(db, c, team_alias_map)
        h_ok = name_any_match(ha, home_list)
        a_ok = name_any_match(aa, away_list)
        if h_ok and a_ok:
            return c, "fuzzy_name"
        if c.is_swapped and name_any_match(ha, away_list) and name_any_match(aa, home_list):
            return c, "fuzzy_swapped"
    return None, "new"


async def process_one_window(idx: int, w_start: datetime, w_end: datetime) -> dict:
    start_date_s = w_start.strftime("%Y-%m-%d")
    end_date_s = w_end.strftime("%Y-%m-%d")
    # 分页拉
    all_jc: list[dict] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        total, matches, tp = pull_result_page(start_date_s, end_date_s, page)
        if page == 1:
            total_pages = tp or 1
        if not matches:
            break
        all_jc.extend(matches)
        if page >= total_pages:
            break
        page += 1
    tag = f"[#{idx:02d}]"
    print(f"\n{tag} ============= 窗口 {start_date_s} ~ {end_date_s}  RESULT_API={len(all_jc)} 条 =============")
    matched = 0  # 匹配上已有的本地 Match
    already_had = 0  # 之前就有 jc_match_id
    created = 0  # 新建了 Match
    no_match = 0  # 真失败
    # Reason codes
    codes: dict[str, int] = {}
    async with async_session() as db:
        for jc in all_jc:
            jc_match_id = str(jc.get("matchId"))
            match_num = jc.get("matchNumStr")
            best, code = await match_local(db, jc)
            codes[code] = codes.get(code, 0) + 1
            if code == "existing":
                already_had += 1
                continue
            if best is None and code == "new":
                # 直接新建 Match
                try:
                    best = await create_match_from_result_api(db, jc)
                    created += 1
                    code = "created"
                except Exception as e:
                    no_match += 1
                    if no_match <= 10:
                        print(f"  {tag} [FAIL CREATE] jc={jc_match_id} num={match_num} {jc.get('matchDate')} {jc.get('allHomeTeam')} vs {jc.get('allAwayTeam')} err={e}")
                    continue
            if best is None:
                no_match += 1
                continue
            # 回填
            updated = False
            if not best.jc_match_id:
                best.jc_match_id = jc_match_id
                updated = True
            if not best.match_num and match_num:
                best.match_num = match_num
                updated = True
            # 顺便填赛果（如果还没比分）
            h_s, a_s = _parse_sections_score(jc.get("sectionsNo999"))
            if h_s is not None and a_s is not None and best.home_score is None:
                best.home_score = h_s
                best.away_score = a_s
                best.status = "finished"
                updated = True
            h_h, a_h = _parse_sections_score(jc.get("sectionsNo1"))
            if h_h is not None and a_h is not None and best.half_home_score is None:
                best.half_home_score = h_h
                best.half_away_score = a_h
                updated = True
            if updated and code != "created":
                db.add(best)
            if code.startswith("num") or code.startswith("fuzzy"):
                matched += 1
        await db.commit()
    summary = f"匹配 {matched} / 已存在 {already_had} / 新建 {created} / 无匹配 {no_match}  (RESULT总数 {len(all_jc)})  reason_codes={codes}"
    print(f"{tag} {summary}")
    return {
        "idx": idx, "start": w_start, "end": w_end,
        "total_api": len(all_jc), "matched": matched,
        "already": already_had, "created": created, "nomatch": no_match,
        "summary": summary, "codes": codes,
    }


async def main():
    ws = list(windows(START, END, STEP_DAYS))
    print(f"总共 {len(ws)} 批：")
    for i, (s, e) in enumerate(ws, 1):
        print(f"  #{i:02d} {s.date()} ~ {e.date()} ({(e-s).days+1}d)")
    stats = []
    for i, (s, e) in enumerate(ws, 1):
        r = await process_one_window(i, s, e)
        stats.append(r)
    print("\n" + "=" * 110)
    print(" 汇总：")
    print("=" * 110)
    sum_total = sum(s["total_api"] for s in stats)
    sum_matched = sum(s["matched"] for s in stats)
    sum_already = sum(s["already"] for s in stats)
    sum_created = sum(s["created"] for s in stats)
    sum_nomatch = sum(s["nomatch"] for s in stats)
    for s in stats:
        print(f"  #{s['idx']:02d} {s['start'].date()} ~ {s['end'].date()}  {s['summary']}")
    print(f"\n总计: RESULT_API 抓取 {sum_total} 条，匹配 {sum_matched} 条，已存在 {sum_already} 条，新建 {sum_created} 条，无法匹配 {sum_nomatch} 条")
    if sum_total:
        ok = sum_matched + sum_already + sum_created
        print(f"      最终 jc_match_id 拥有率 = {ok}/{sum_total} = {100*ok/sum_total:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
