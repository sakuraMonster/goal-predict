# -*- coding: utf-8 -*-
"""模型预测 vs 市场赔率选数的命中对比 + 每日候选充足度（8月，只读）。

问题：市场赔率已定价可预测信息，单特征选场无法提升命中。
验证：
  1) 每天进球腿候选数量 → 是否支持"每日多串"
  2) 模型 total_goals_top3（Dixon-Coles/SNAP等）选数命中 vs 当前市场赔率分层选数
  3) 模拟"每日选模型 top2 场次"的串关命中
"""
import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.api.market_flow import _market_flow_query, _matchday_date
from app.db.database import async_session


async def main():
    start = datetime(2026, 8, 1, 12, 0, 0)
    end = datetime(2026, 9, 1, 12, 0, 0)
    async with async_session() as db:
        items, _os, _osm = await _market_flow_query(
            db, start=start, end=end, ou_tier="standard", ou_sm_tier="standard"
        )
        lines = []

        # 1) 每日候选充足度：TTG/SM 方向信号有效的场次（判大+判小）
        by_day = defaultdict(list)
        for it in items:
            ttg_p_big = (it.get("ou_all") or {}).get("standard", {}).get("p_big")
            ttg_dir = ("over" if (ttg_p_big is not None and ttg_p_big >= 0.62)
                       else "under" if (ttg_p_big is not None and ttg_p_big <= 0.38) else None)
            sm_dir = ((it.get("ou_sm") or {}).get("tiers") or {}).get("standard")
            ttg_valid = ttg_dir if ttg_dir in ("over", "under") else None
            sm_valid = sm_dir if sm_dir in ("over", "under") else None
            if ttg_valid and sm_valid and ttg_valid != sm_valid:
                dirn = None
            else:
                dirn = ttg_valid or sm_valid
            if not dirn:
                continue
            d = _matchday_date(it["kickoff_time"], match_num=it.get("match_num"))
            by_day[d].append(it)

        lines.append("===== 每日候选场次数（TTG/SM 方向信号有效，8月） =====")
        cnts = [len(v) for v in by_day.values()]
        lines.append(f"有信号天数: {len(cnts)}  平均候选: {sum(cnts)/len(cnts):.1f} 场/天")
        lines.append(f"候选>=4场的天数: {sum(1 for c in cnts if c >= 4)}  ({sum(1 for c in cnts if c >= 4)/len(cnts):.0%})")
        lines.append(f"候选>=3场的天数: {sum(1 for c in cnts if c >= 3)}  ({sum(1 for c in cnts if c >= 3)/len(cnts):.0%})")

        # 2) 模型 total_goals_top3 命中（8月全部已结算）
        settled = [it for it in items if it.get("actual_total_goals") is not None]
        hit3 = sum(1 for it in settled if it.get("total_hit_top3"))
        hit2 = sum(1 for it in settled if it.get("total_hit_top2"))
        lines.append(f"\n===== 模型进球数预测命中（{len(settled)}场） =====")
        lines.append(f"模型 top3 命中: {hit3}/{len(settled)} = {hit3/len(settled):.3f}")
        lines.append(f"模型 top2 命中: {hit2}/{len(settled)} = {hit2/len(settled):.3f}")

        # 模型 top3 vs 实际，按联赛
        by_lg = defaultdict(list)
        for it in settled:
            by_lg[it.get("league_name")].append(it)
        lines.append("--- 模型 top3 命中按联赛 ---")
        for lg, grp in sorted(by_lg.items(), key=lambda kv: -sum(1 for g in kv[1] if g.get("total_hit_top3")) / len(kv[1])):
            if len(grp) < 8:
                continue
            h = sum(1 for g in grp if g.get("total_hit_top3"))
            lines.append(f"  {lg}: n={len(grp)} 命中={h} p={h/len(grp):.3f}")

        # 3) 模拟：每日选模型 top3 命中率最高的 2 场做进球腿（无方向腿，只看进球腿组合）
        #    对比：市场赔率分层选数（345 档）的 2 场命中
        lines.append("\n===== 每日选 2 场：模型 top3 场次命中 vs 市场 345 命中 =====")
        # 模型侧：字段 total_goals_top3 是预测进球数列表，命中判定已有 total_hit_top3
        # 模拟"每日选模型置信度最高的 2 场"需要置信度字段——模型没有直接置信度，用 top3 命中的事后概率近似说明问题
        # 这里只统计：模型 top3 命中的场次里，方向信号是否也有效（重叠度）
        sig_ok = set()
        for it in items:
            ttg_p_big = (it.get("ou_all") or {}).get("standard", {}).get("p_big")
            ttg_dir = ("over" if (ttg_p_big is not None and ttg_p_big >= 0.62)
                       else "under" if (ttg_p_big is not None and ttg_p_big <= 0.38) else None)
            if ttg_dir in ("over", "under"):
                sig_ok.add(it["match_id"])
        both = [it for it in settled if it.get("total_hit_top3") and it["match_id"] in sig_ok]
        lines.append(f"模型 top3 命中且方向信号有效: {len(both)}/{hit3} = {len(both)/hit3:.2%}")

        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_model_pick_report.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"报告已写入 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
