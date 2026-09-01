"""回填半场比分：对已完场但 half_home_score 为 NULL 的场次，从竞彩赛果接口补 sectionsNo1。

用法:
    python tools/backfill_half_scores.py --start 2026-08-01 --end 2026-08-31
"""
import argparse
import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.dirname(HERE)
sys.path.insert(0, BACKEND_ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_ROOT, ".env"))

import requests
from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Match

RESULT_API = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.sporttery.cn/jc/zqsgkj/",
}


def _parse_score(s):
    if not s:
        return None, None
    m = re.match(r"^\s*(\d+)\s*[:：]\s*(\d+)\s*$", str(s))
    if not m:
        return None, None
    try:
        return int(m.group(1)), int(m.group(2))
    except Exception:
        return None, None


def pull_result_page(start_date, end_date, page):
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


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--end", default="2026-08-31")
    args = ap.parse_args()

    all_jc = []
    page = 1
    while True:
        total, matches, pages = pull_result_page(args.start, args.end, page)
        all_jc.extend(matches)
        if page >= pages or not matches:
            break
        page += 1
    print(f"拉取赛果 {len(all_jc)} 条 ({args.start} ~ {args.end})")

    async with async_session() as db:
        missing = (await db.execute(
            select(Match).where(
                Match.home_score.isnot(None),
                Match.half_home_score.is_(None),
                Match.jc_match_id.isnot(None),
            )
        )).scalars().all()
        by_jc = {m.jc_match_id: m for m in missing}
        print(f"本地半场比分缺失 {len(missing)} 场（全局）")

        filled = 0
        no_section = 0
        for jc in all_jc:
            mid = str(jc.get("matchId"))
            if mid not in by_jc:
                continue
            h_h, a_h = _parse_score(jc.get("sectionsNo1"))
            if h_h is None or a_h is None:
                no_section += 1
                continue
            m = by_jc[mid]
            m.half_home_score = h_h
            m.half_away_score = a_h
            db.add(m)
            filled += 1
        await db.commit()
        print(f"回填半场比分 {filled} 场，赛果无 sectionsNo1 {no_section} 场")


if __name__ == "__main__":
    asyncio.run(main())
