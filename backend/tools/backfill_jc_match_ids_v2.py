""" 回填 2026-01-01 ~ 2026-08-03 Match.jc_match_id / match_num.

V2 修复匹配率 0%：Match.home/away_team_name 可能是英文（Sportmonks 导入期），
必须回退 Team.name_zh (中文官方名) 对比 RESULT_API 的 allHomeTeam/allAwayTeam（中文全名）。
也加入 Team.name_en vs RESULT_API 队名字段别名匹配兜底。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.dirname(HERE)
sys.path.insert(0, BACKEND_ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_ROOT, ".env"))

from sqlalchemy import select, and_, or_, func
from app.db.database import async_session
from app.db.models import Match, Team

RESULT_API = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.sporttery.cn/jc/zqsgkj/",
}

START = datetime(2026, 1, 1, 0, 0, 0)
END   = datetime(2026, 8, 3, 23, 59, 59)
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

def norm(s: str | None) -> str:
    if not s:
        return ""
    s = _NAME_STRIP.sub("", s.strip())
    s = s.replace("U23", "u23").replace("U21", "u21").replace("U19", "u19").replace("U17", "u17")
    s = s.replace("女足", "women")
    return s.lower()


def name_any_match(a_list: list[str], b_list: list[str]) -> bool:
    """ a_list=[中文别名1,中文别名2,英文名], b_list=[中文allName,中文名,简称] 任一对匹配则 True """
    norm_a = [norm(x) for x in a_list if x]
    norm_b = [norm(x) for x in b_list if x]
    norm_a = [x for x in norm_a if x]
    norm_b = [x for x in norm_b if x]
    short_a = [re.sub(r"fc$|sc$|ac$|cf$", "", x) or x for x in norm_a]
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
            # 首3 + 尾3 字符重合
            if len(sa) >= 4 and len(sb) >= 4:
                if sa[:3] == sb[:3] and sa[-3:] == sb[-3:]:
                    return True
    return False


async def names_of(db, m: Match) -> tuple[list[str], list[str]]:
    """ return (home_aliases, away_aliases) 每个都是 [Match.name, Team.name_zh, Team.name_en] 再去重 """
    ht = None
    at = None
    if m.home_team_id:
        ht = (await db.get(Team, m.home_team_id))
    if m.away_team_id:
        at = (await db.get(Team, m.away_team_id))
    def _agg(*vals):
        seen = set()
        out = []
        for v in vals:
            if not v:
                continue
            nv = norm(v)
            if nv and nv not in seen:
                seen.add(nv)
                out.append(v)
        return out
    home_aliases = _agg(
        m.home_team_name,
        ht.name_zh if ht else None,
        ht.name_en if ht else None,
        (ht.name_en + " " + ht.name_zh) if ht else None,
    )
    away_aliases = _agg(
        m.away_team_name,
        at.name_zh if at else None,
        at.name_en if at else None,
    )
    return home_aliases, away_aliases


async def match_local(db, jc: dict, tag: str, verbose_nomatch: bool, nomatch_logger) -> Match | None:
    match_num = (jc.get("matchNumStr") or "").strip() or None
    jc_match_id = jc.get("matchId")
    home_all = jc.get("allHomeTeam") or ""
    home_short = jc.get("homeTeam") or ""
    away_all = jc.get("allAwayTeam") or ""
    away_short = jc.get("awayTeam") or ""
    home_list = [home_all, home_short]
    away_list = [away_all, away_short]
    match_date_s = (jc.get("matchDate") or "").strip()
    if not jc_match_id:
        return None
    try:
        md = datetime.strptime(match_date_s, "%Y-%m-%d").date()
    except Exception:
        return None
    # 0) 已回填过？全局跳过
    existing = (await db.execute(
        select(Match).where(Match.jc_match_id == str(jc_match_id))
    )).scalar_one_or_none()
    if existing is not None:
        return None
    # 1) match_num 精确匹配 (最优先) + 日期 ± 2天
    if match_num:
        r2 = (await db.execute(
            select(Match).where(and_(
                Match.match_num == match_num,
                Match.kickoff_time >= datetime.combine(md, datetime.min.time()) - timedelta(days=3),
                Match.kickoff_time <= datetime.combine(md, datetime.min.time()) + timedelta(days=3),
            ))
        )).scalars().all()
        for c in r2:
            ha, aa = await names_of(db, c)
            if name_any_match(ha, home_list) and name_any_match(aa, away_list):
                return c
            # swapped 可能吗 (队名 swapped + is_swapped == True)
            if name_any_match(ha, away_list) and name_any_match(aa, home_list) and c.is_swapped:
                return c
        if len(r2) == 1:
            return r2[0]
    # 2) fuzzy: kickoff + date ± 1天, jc_match_id 空, 队名相似
    kickoff_low = datetime.combine(md, datetime.min.time()) - timedelta(days=1)
    kickoff_high = datetime.combine(md, datetime.min.time()) + timedelta(days=2, hours=23, minutes=59, seconds=59)
    cand = (await db.execute(
        select(Match).where(and_(
            Match.kickoff_time >= kickoff_low,
            Match.kickoff_time <= kickoff_high,
            or_(Match.jc_match_id.is_(None), Match.jc_match_id == ""),
        ))
    )).scalars().all()
    best = None
    best_score = 0
    for c in cand:
        ha, aa = await names_of(db, c)
        h_ok = name_any_match(ha, home_list)
        a_ok = name_any_match(aa, away_list)
        if h_ok and a_ok:
            return c
        swapped_ok = False
        if c.is_swapped:
            if name_any_match(ha, away_list) and name_any_match(aa, home_list):
                swapped_ok = True
        if swapped_ok:
            return c
        # 记录最高分但没 100% match 的，最后 verbose nomatch 不打印时直接 None
    return None


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
    matched = 0
    already_had = 0
    no_match = 0
    nomatch_sample: list[str] = []
    async with async_session() as db:
        for jc in all_jc:
            jc_match_id = str(jc.get("matchId"))
            match_num = jc.get("matchNumStr")
            verbose_nm = no_match < 20
            def _log(x):
                nomatch_sample.append(x)
                if verbose_nm:
                    print(f"  {tag} [nomatch] jc={jc_match_id} num={match_num} date={jc.get('matchDate')} {jc.get('allHomeTeam') or jc.get('homeTeam')} vs {jc.get('allAwayTeam') or jc.get('awayTeam')}")
            best = await match_local(db, jc, tag, verbose_nm, _log)
            if best is None:
                is_already = (await db.execute(
                    select(func.count()).select_from(Match).where(Match.jc_match_id == jc_match_id)
                )).scalar_one() or 0
                if is_already:
                    already_had += 1
                else:
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
            if updated:
                db.add(best)
            matched += 1
        await db.commit()
    summary = f"匹配 {matched} / 已存在 {already_had} / 无匹配 {no_match}  (RESULT总数 {len(all_jc)})"
    print(f"{tag} {summary}")
    return {
        "idx": idx, "start": w_start, "end": w_end,
        "total_api": len(all_jc), "matched": matched,
        "already": already_had, "nomatch": no_match,
        "summary": summary,
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
    print("\n" + "=" * 90)
    print(" 汇总：")
    print("=" * 90)
    sum_total = sum(s["total_api"] for s in stats)
    sum_matched = sum(s["matched"] for s in stats)
    sum_already = sum(s["already"] for s in stats)
    sum_nomatch = sum(s["nomatch"] for s in stats)
    for s in stats:
        print(f"  #{s['idx']:02d} {s['start'].date()} ~ {s['end'].date()}  {s['summary']}")
    print(f"\n总计: RESULT_API 抓取 {sum_total} 条，匹配 {sum_matched} 条，已存在 {sum_already} 条，无法匹配 {sum_nomatch} 条")
    if sum_total:
        print(f"      匹配率 = {sum_matched}/{sum_total} = {100*sum_matched/sum_total:.1f}%")
        print(f"      有效覆盖率(匹配+已存在)/总计 = {(sum_matched+sum_already)}/{sum_total} = {100*(sum_matched+sum_already)/sum_total:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
