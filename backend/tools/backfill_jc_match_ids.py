""" 回填 2026-01-01 ~ 2026-08-03 Match.jc_match_id / match_num.

数据源: 竞彩 getUniformMatchResultV1 (RESULT_API)
策略: 每次 29 天, RESULT_API 分页拉取历史赛果列表 -> 模糊匹配本地 Match
      匹配成功 update Match.jc_match_id, Match.match_num.
匹配顺序:
  1. (精确) match_num_exact == Match.match_num   AND kickoff ∈ [date 00:00, date+1 day 23:59]
  2. (半精确) jc_match_id already exists  → 跳过 (已在有值的窗口里不用回填)
  3. (fuzzy) 主/客队名 模糊 + kickoff ∈ [date-1 day, date+2 day]
匹配键用 (主名,客名,开球日±1天) 的候选列表，再做「子串/去空格数字符号」相似度比较。
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
from app.db.models import Match

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
    s = s.replace("女足", "women").replace("女足", "women")
    return s.lower()


def name_match(a_raw: str | None, b_raw: str | None) -> bool:
    a, b = norm(a_raw), norm(b_raw)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return True
    # 首尾 3 字符都相同认为匹配 (去除前后常见的 FC, SC, AC 等)
    short_a = a.replace("fc", "").replace("sc", "").replace("ac", "").replace("cf", "") or a
    short_b = b.replace("fc", "").replace("sc", "").replace("ac", "").replace("cf", "") or b
    if short_a and short_b and len(short_a) >= 3 and len(short_b) >= 3:
        if short_a == short_b:
            return True
        if short_a in short_b or short_b in short_a:
            return True
        if short_a[:3] == short_b[:3] and short_a[-3:] == short_b[-3:]:
            return True
    return False


async def match_local(db, jc: dict) -> Match | None:
    """jc = RESULT_API 一条 record """
    match_num = (jc.get("matchNumStr") or "").strip() or None
    jc_match_id = jc.get("matchId")
    home_name = jc.get("allHomeTeam") or jc.get("homeTeam") or ""
    away_name = jc.get("allAwayTeam") or jc.get("awayTeam") or ""
    match_date_s = (jc.get("matchDate") or "").strip()  # YYYY-MM-DD
    if not jc_match_id:
        return None
    try:
        md = datetime.strptime(match_date_s, "%Y-%m-%d").date()
    except Exception:
        return None
    # 1) 如果本地 Match 已经填了这个 jc_match_id，跳过
    existing = (await db.execute(
        select(Match).where(Match.jc_match_id == str(jc_match_id))
    )).scalar_one_or_none()
    if existing is not None:
        return None  # 已经有了，返回 None 让外层不重复 update
    # 2) match_num 精确匹配 + 日期范围
    if match_num:
        r2 = (await db.execute(
            select(Match).where(and_(
                Match.match_num == match_num,
                Match.kickoff_time >= datetime.combine(md, datetime.min.time()) - timedelta(days=1),
                Match.kickoff_time <= datetime.combine(md, datetime.min.time()) + timedelta(days=2),
            ))
        )).scalars().all()
        # 再在候选里找队名最像的
        best = None
        for c in r2:
            if name_match(home_name, c.home_team_name) and name_match(away_name, c.away_team_name):
                best = c; break
        if best is None and len(r2) == 1:
            best = r2[0]
        if best is not None:
            return best
    # 3) fuzzy: 队名 + kickoff ∈ [md-1, md+2]
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
    for c in cand:
        h_ok = name_match(home_name, c.home_team_name)
        a_ok = name_match(away_name, c.away_team_name)
        # 主队匹配 A 客队匹配 B :  正常顺序
        if h_ok and a_ok:
            best = c; break
        # 竞彩有时候 主客顺序有歧义？ (对赛果赛果 RESULT_API 一般不 swap，Sportmonks 有 swapped 但应该在 home/away_team_name 体现)
        # 尝试 swapped（主队名 对客场队名 + 客队名 对主队名）+ Match.is_swapped=True
        if name_match(home_name, c.away_team_name) and name_match(away_name, c.home_team_name):
            # 只在 is_swapped=True 的时候考虑 swapped 匹配，避免把没 swapped 的比赛乱映射
            if c.is_swapped:
                best = c; break
    return best


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
    # 逐条匹配
    matched = 0
    already_had = 0
    no_match = 0
    async with async_session() as db:
        for jc in all_jc:
            jc_match_id = str(jc.get("matchId"))
            match_num = jc.get("matchNumStr")
            best = await match_local(db, jc)
            if best is None:
                # 判断到底是"已存在"还是"真没匹配到"
                is_already = (await db.execute(
                    select(func.count()).select_from(Match).where(Match.jc_match_id == jc_match_id)
                )).scalar_one() or 0
                if is_already:
                    already_had += 1
                else:
                    no_match += 1
                    if no_match <= 10:
                        print(f"  {tag} [nomatch] jc={jc_match_id} num={match_num} date={jc.get('matchDate')} {jc.get('allHomeTeam') or jc.get('homeTeam')} vs {jc.get('allAwayTeam') or jc.get('awayTeam')}")
                continue
            # 回填
            if not best.jc_match_id:
                best.jc_match_id = jc_match_id
            if not best.match_num and match_num:
                best.match_num = match_num
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
    print(f"      匹配率 = {sum_matched}/{sum_total} = {100*sum_matched/sum_total if sum_total else 0:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
