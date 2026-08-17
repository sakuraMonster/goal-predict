"""补充：模型/市场置信度校准 + 分歧方向类型 → 命中率（近30天 id>=15000）
输出: _cold_upside_calib.txt
"""
import asyncio, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cold_upside_calib.txt")
OUT = []
def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

DIRS = {0: "主胜", 1: "平局", 2: "客胜"}
W_START = datetime(2026, 7, 16, 12, 0)
W_END = datetime(2026, 8, 15, 12, 0)

def implied_from_odds(h, d, a):
    if not h or not d or not a or min(h, d, a) <= 1.01:
        return None
    ih, id_, ia = 1/h, 1/d, 1/a
    t = ih + id_ + ia
    return ih/t, id_/t, ia/t

async def main():
    async with async_session() as db:
        rows = (await db.execute(
            select(Prediction, Match).join(Match, Prediction.match_id == Match.id)
            .where(Prediction.result_spf != 0)
            .where(Match.kickoff_time >= W_START, Match.kickoff_time < W_END)
        )).all()
        match_ids = [p.match_id for p, _ in rows]
        snaps = (await db.execute(select(OddsSnapshot).where(OddsSnapshot.match_id.in_(match_ids)))).scalars().all()
        ko = {p.match_id: m.kickoff_time for p, m in rows}
        by_m = defaultdict(list)
        for s in snaps:
            if ko.get(s.match_id) and s.snapshot_time < ko[s.match_id]:
                by_m[s.match_id].append(s)
        last = {}
        for mid, ss in by_m.items():
            ss.sort(key=lambda s: s.snapshot_time)
            t = ss[-1].snapshot_time
            tail = [s for s in ss if s.snapshot_time == t]
            h = np.mean([s.home_win for s in tail if s.home_win])
            d = np.mean([s.draw for s in tail if s.draw])
            a = np.mean([s.away_win for s in tail if s.away_win])
            last[mid] = (h, d, a)

        samples = []
        for pred, m in rows:
            if pred.match_id < 15000:  # 只看当前链路
                continue
            if pred.actual_home_score is None or not (pred.home_prob and pred.draw_prob and pred.away_prob):
                continue
            imp = implied_from_odds(*last.get(pred.match_id, (None, None, None)))
            if not imp:
                continue
            probs = [pred.home_prob, pred.draw_prob, pred.away_prob]
            act = 0 if pred.actual_home_score > pred.actual_away_score else (
                1 if pred.actual_home_score == pred.actual_away_score else 2)
            md = int(np.argmax(probs))
            mf = int(np.argmax(imp))
            samples.append({
                "probs": probs, "imp": imp, "md": md, "mf": mf, "act": act,
                "league": m.league.name_zh if m.league else "?",
                "home": m.home_team.name_zh if m.home_team else m.home_team_name,
                "away": m.away_team.name_zh if m.away_team else m.away_team_name,
            })
        N = len(samples)
        log(f"id>=15000 有效样本: {N}")

        # 模型 max prob 分桶
        log(f"\n===== 模型 max prob 分桶（模型方向命中率）=====")
        buckets = defaultdict(lambda: [0, 0])
        for s in samples:
            mp = max(s["probs"])
            b = min(0.6, max(0.35, round(mp * 20) / 20))
            buckets[b][0] += 1
            buckets[b][1] += 1 if s["md"] == s["act"] else 0
        for b in sorted(buckets):
            n, h = buckets[b]
            if n >= 2:
                log(f"  max_p={b:.2f}: n={n}, 命中 {h/n:.1%}")

        # 市场热门隐含概率分桶
        log(f"\n===== 市场热门隐含概率分桶（市场热门命中率）=====")
        buckets = defaultdict(lambda: [0, 0])
        for s in samples:
            mp = s["imp"][s["mf"]]
            b = min(0.6, max(0.35, round(mp * 20) / 20))
            buckets[b][0] += 1
            buckets[b][1] += 1 if s["mf"] == s["act"] else 0
        for b in sorted(buckets):
            n, h = buckets[b]
            if n >= 2:
                log(f"  imp_p={b:.2f}: n={n}, 命中 {h/n:.1%}")

        # 分歧方向类型
        log(f"\n===== 分歧方向类型（id>=15000）=====")
        div = [s for s in samples if s["md"] != s["mf"]]
        types = defaultdict(lambda: [0, 0])
        for s in div:
            t = f"模{DIRS[s['md']]}→市{DIRS[s['mf']]}"
            types[t][0] += 1
            types[t][1] += 1 if s["md"] == s["act"] else 0
        for t, (n, h) in sorted(types.items(), key=lambda x: -x[1][0]):
            log(f"  {t}: n={n}, 博冷命中 {h}/{n} = {h/n:.1%}")

        # 模型方向为平局 vs 非平局（全部样本）
        log(f"\n===== 模型预测方向为平局 的命中率（模型平局是否低质）=====")
        for label, cond in [("模型推平局", lambda s: s["md"] == 1),
                            ("模型推胜负", lambda s: s["md"] != 1)]:
            sub = [s for s in samples if cond(s)]
            h = sum(1 for s in sub if s["md"] == s["act"])
            log(f"  {label}: {len(sub)} 场, 命中 {h/len(sub):.1%}" if sub else f"  {label}: 0")

        # 实际平局率
        dr = sum(1 for s in samples if s["act"] == 1)
        log(f"\n实际平局率: {dr/N:.1%}（{dr}/{N}）")

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        log(f"\n已写入 {REPORT_PATH}")

asyncio.run(main())
