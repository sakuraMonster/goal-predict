# -*- coding: utf-8 -*-
"""对比「strict优先退档standard」vs「当前standard」的方向判定与命中率（只读）。

模式A(当前): TTG standard门控 + SM O/U standard门控
模式B(新)  : TTG strict优先(skip退standard) + SM O/U strict优先(skip退standard)
输出: 各模式判大/判小场次、方向命中率、覆盖、方向判定差异场次清单
"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.api.market_flow import _market_flow_query
from app.db.database import async_session


async def main():
    _win = os.getenv("WIN", "aug")
    if _win == "jul":
        start = datetime(2026, 7, 1, 12, 0, 0)
        end = datetime(2026, 8, 1, 12, 0, 0)
    else:
        start = datetime(2026, 8, 1, 12, 0, 0)
        end = datetime(2026, 9, 1, 12, 0, 0)
    async with async_session() as db:
        items, _os, _osm = await _market_flow_query(
            db, start=start, end=end, ou_tier="standard", ou_sm_tier="standard"
        )
        lines = []
        lines.append(f"===== strict优先退档standard vs 当前standard（{_win}） =====")

        stats = {"A": {"over": [0, 0], "under": [0, 0]}, "B": {"over": [0, 0], "under": [0, 0]}}
        diff_rows = []

        def _final_dir(it, mode):
            """返回 (ttg_dir, sm_dir, dirn)。mode: A=standard, B=strict优先退standard"""
            ou_all = it.get("ou_all") or {}
            sm_tiers = ((it.get("ou_sm") or {}).get("tiers") or {})
            if mode == "A":
                ttg_p_big = ou_all.get("standard", {}).get("p_big")
                ttg_dir = ("over" if (ttg_p_big is not None and ttg_p_big >= 0.62)
                           else "under" if (ttg_p_big is not None and ttg_p_big <= 0.38) else None)
                sm_dir = sm_tiers.get("standard")
            else:
                ttg_dir = None
                for tk in ("strict", "standard"):
                    _d = (ou_all.get(tk) or {}).get("direction")
                    if _d in ("over", "under"):
                        ttg_dir = _d
                        break
                sm_dir = None
                for tk in ("strict", "standard"):
                    _d = sm_tiers.get(tk)
                    if _d in ("over", "under"):
                        sm_dir = _d
                        break
            ttg_valid = ttg_dir if ttg_dir in ("over", "under") else None
            sm_valid = sm_dir if sm_dir in ("over", "under") else None
            if ttg_valid and sm_valid and ttg_valid != sm_valid:
                dirn = None
            else:
                dirn = ttg_valid or sm_valid
            return ttg_dir, sm_dir, dirn

        for it in items:
            actual_over = None
            if it.get("actual_total_goals") is not None:
                actual_over = it["actual_total_goals"] > 2.5
            settled = actual_over is not None
            _, _, dirn_a = _final_dir(it, "A")
            _, _, dirn_b = _final_dir(it, "B")
            for mode, dirn in (("A", dirn_a), ("B", dirn_b)):
                if dirn in ("over", "under") and settled:
                    s = stats[mode][dirn]
                    s[0] += 1
                    if (actual_over if dirn == "over" else not actual_over):
                        s[1] += 1
            if dirn_a != dirn_b:
                diff_rows.append((it.get("match_num"), it.get("home_team"), it.get("away_team"),
                                  dirn_a, dirn_b, str(it.get("actual_total_goals")) if settled else "-"))

        for mode, name in (("A", "A:当前standard"), ("B", "B:strict优先退standard")):
            lines.append(f"\n--- {name} ---")
            for d in ("over", "under"):
                n, h = stats[mode][d]
                p = h / n if n else 0
                lines.append(f"  {d}: n={n} 命中={h} p={p:.3f}")
            tot_n = stats[mode]["over"][0] + stats[mode]["under"][0]
            tot_h = stats[mode]["over"][1] + stats[mode]["under"][1]
            lines.append(f"  合计: n={tot_n} 命中={tot_h} p={tot_h/tot_n if tot_n else 0:.3f}")

        lines.append(f"\n===== 方向判定差异场次（{len(diff_rows)} 场） =====")
        for r in diff_rows:
            lines.append(f"  {r[0]} {r[1]}vs{r[2]}: A={r[3]} B={r[4]} 实际={r[5]}")

        # 进球腿候选（判大）影响：A/B 判大场次差集
        lines.append("\n===== 判大场次差集（A判大 vs B判大） =====")
        set_a = set()
        set_b = set()
        for it in items:
            _, _, da = _final_dir(it, "A")
            _, _, db_ = _final_dir(it, "B")
            if da == "over":
                set_a.add(it.get("match_num") or it["kickoff_time"])
            if db_ == "over":
                set_b.add(it.get("match_num") or it["kickoff_time"])
        lines.append(f"A判大={len(set_a)} 场, B判大={len(set_b)} 场")
        only_b = set_b - set_a
        only_a = set_a - set_b
        lines.append(f"B新增判大 {len(only_b)} 场（A为skip或矛盾）")
        lines.append(f"A判大但B不是 {len(only_a)} 场")
        for mn in sorted(only_a | only_b):
            it = next((x for x in items if (x.get("match_num") or x["kickoff_time"]) == mn), None)
            if it:
                _, _, da = _final_dir(it, "A")
                _, _, db_ = _final_dir(it, "B")
                lines.append(f"  {mn} {it.get('home_team')}vs{it.get('away_team')}: A={da} B={db_}")

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_ou_tier_switch_{_win}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("report ->", out_path)


if __name__ == "__main__":
    asyncio.run(main())
