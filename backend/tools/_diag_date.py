# -*- coding: utf-8 -*-
"""诊断指定日期为何三方案为空：统计 TTG/SM 方向信号、腿池、方向腿（只读）。"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.api.market_flow import _market_flow_query, _matchday_date
from app.db.database import async_session


async def main():
    date_s = os.getenv("DATE", "2026-09-01")
    d0 = datetime.fromisoformat(date_s)
    start = d0.replace(hour=12, minute=0, second=0, microsecond=0)
    end = start + __import__("datetime").timedelta(days=1)
    async with async_session() as db:
        items, _os, _osm = await _market_flow_query(
            db, start=start, end=end, ou_tier="standard", ou_sm_tier="standard"
        )
        lines = []
        lines.append(f"items({date_s}) = {len(items)}")
        n_ttg_sig = n_sm_sig = n_ttg_over = n_ttg_under = n_sm_over = n_sm_under = 0
        n_dir_fav = n_dir_amb = n_dir_upset = 0
        n_hafu = 0
        n_ttg_odds = 0
        for it in items:
            ou_all = it.get("ou_all") or {}
            ttg_std = (ou_all.get("standard") or {}).get("direction")
            ttg_strict = (ou_all.get("strict") or {}).get("direction")
            sm_tiers = ((it.get("ou_sm") or {}).get("tiers") or {})
            sm_std = sm_tiers.get("standard")
            sm_strict = sm_tiers.get("strict")
            if ttg_std in ("over", "under"):
                n_ttg_sig += 1
                if ttg_std == "over":
                    n_ttg_over += 1
                else:
                    n_ttg_under += 1
            if sm_std in ("over", "under"):
                n_sm_sig += 1
                if sm_std == "over":
                    n_sm_over += 1
                else:
                    n_sm_under += 1
            if it.get("ttg_odds"):
                n_ttg_odds += 1
            pool = it.get("pool")
            if pool == "favorite":
                n_dir_fav += 1
            elif pool == "ambiguous":
                n_dir_amb += 1
            elif pool == "upset":
                n_dir_upset += 1
            if it.get("hafu_odds"):
                n_hafu += 1
            lines.append(f"  {it.get('match_num')} {it.get('home_team')}vs{it.get('away_team')} pool={pool} "
                         f"ttg_strict={ttg_strict} ttg_std={ttg_std} sm_strict={sm_strict} sm_std={sm_std} "
                         f"has_ttg={bool(it.get('ttg_odds'))} has_hafu={bool(it.get('hafu_odds'))}")
        lines.append("")
        lines.append(f"TTG方向信号={n_ttg_sig} (大{n_ttg_over}/小{n_ttg_under}) | SM方向信号={n_sm_sig} (大{n_sm_over}/小{n_sm_under})")
        lines.append(f"方向腿池: favorite={n_dir_fav} ambiguous={n_dir_amb} upset={n_dir_upset}")
        lines.append(f"有TTG赔率={n_ttg_odds} 有半全场赔率={n_hafu}")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_diag_{date_s}.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out)


if __name__ == "__main__":
    asyncio.run(main())
