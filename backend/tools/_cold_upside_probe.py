"""近30天 模型反转博冷实证 + 冷门优选信号验证（v2，按 id 段分层）
口径：
- 近30天比赛周期: kickoff_time ∈ [2026-07-16 12:00, 2026-08-15 12:00)（北京时间 naive）
- 已结算 + 有有效概率(home/draw/away 非空) + 有赛前快照
- 市场隐含概率：odds_snapshots 中 snapshot_time < kickoff_time 的最后一刻、全部 bookmaker 平均去水
- 模型方向 = argmax(home/draw/away_prob)；冷门/分歧 = 模型方向 != 市场热门方向
- 分层：id<1000（早期旧模型补跑） vs id>=15000（当前链路）
输出：_cold_upside_report.txt
"""
import asyncio, os
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np

import sys
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cold_upside_report.txt")
OUT = []
def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

DIRS = {0: "主胜", 1: "平局", 2: "客胜"}
W_START = datetime(2026, 7, 16, 12, 0)
W_END = datetime(2026, 8, 15, 12, 0)

def implied_from_odds(h, d, a):
    if not h or not d or not a:
        return None
    if min(h, d, a) <= 1.01:
        return None
    ih, id_, ia = 1/h, 1/d, 1/a
    tot = ih + id_ + ia
    return ih/tot, id_/tot, ia/tot

def bucket(samples, key="cold_gap"):
    buckets = defaultdict(lambda: [0, 0])
    for s in samples:
        g = max(-0.5, min(0.5, s[key]))
        b = round(g * 20) / 20
        buckets[b][0] += 1
        buckets[b][1] += s["cold_ok"]
    return buckets

def report_group(title, samples):
    N = len(samples)
    if N == 0:
        log(f"\n[{title}] 无样本")
        return
    model_hit = sum(1 for s in samples if s["model_dir"] == s["actual"])
    market_hit = sum(1 for s in samples if s["market_fav"] == s["actual"])
    agree = [s for s in samples if s["model_dir"] == s["market_fav"]]
    diverge = [s for s in samples if s["model_dir"] != s["market_fav"]]
    ah = sum(1 for s in agree if s["model_dir"] == s["actual"])
    dh_fav = sum(1 for s in diverge if s["market_fav"] == s["actual"])
    dh_cold = sum(1 for s in diverge if s["model_dir"] == s["actual"])
    cold_rate = sum(s["cold_ok"] for s in samples) / N
    log(f"\n===== [{title}] n={N} =====")
    log(f"模型A argmax 命中率: {model_hit}/{N} = {model_hit/N:.1%}")
    log(f"市场热门方向命中率: {market_hit}/{N} = {market_hit/N:.1%}")
    log(f"冷门发生率(市场热门失败): {cold_rate:.1%}")
    log(f"一致场(模型==热门): {len(agree)} 场, 模型命中 {ah} = {ah/len(agree):.1%}" if agree else "一致场 0")
    if diverge:
        log(f"分歧场(模型≠热门): {len(diverge)}")
        log(f"  分歧场中市场热门命中: {dh_fav} = {dh_fav/len(diverge):.1%}")
        log(f"  分歧场中模型冷门命中: {dh_cold} = {dh_cold/len(diverge):.1%}")
        imp_avg = np.mean([s["imp"][s["model_dir"]] for s in diverge])
        log(f"  博冷方向隐含概率均值 {imp_avg:.1%} → 价值 {(dh_cold/len(diverge) - imp_avg):+.1%}")
    # 反转
    rev_hit = 0
    for s in samples:
        md = s["model_dir"]
        rev_dir = max((x for x in (0, 1, 2) if x != md), key=lambda x: s["probs"][x])
        if rev_dir == s["actual"]:
            rev_hit += 1
    log(f"全量反转(买模型反面): {rev_hit}/{N} = {rev_hit/N:.1%}")
    # 冷门优选信号
    colds = sorted(samples, key=lambda s: -s["cold_gap"])
    for frac, label in [(0.2, "Top20%"), (0.3, "Top30%"), (0.5, "Top50%")]:
        k = max(1, int(N * frac))
        seg = colds[:k]
        c = sum(1 for s in seg if s["cold_ok"])
        log(f"  cold_gap {label}(n={k}): 冷门率 {c/k:.1%}")
    # 每日 top_n
    day_of = defaultdict(list)
    for s in samples:
        kt = s["kickoff"]
        day = kt.date() if kt.hour >= 12 else (kt - timedelta(hours=12)).date()
        day_of[day].append(s)
    for top_n in (2, 3):
        picks, hits = 0, 0
        for day, ds in day_of.items():
            for s in sorted(ds, key=lambda x: -x["cold_gap"])[:top_n]:
                picks += 1
                hits += s["cold_ok"]
        log(f"  每日 top{top_n}: {hits}/{picks} = {hits/picks:.1%} (冷门率)" if picks else f"  每日 top{top_n}: 无")

async def main():
    async with async_session() as db:
        rows = (await db.execute(
            select(Prediction, Match).join(Match, Prediction.match_id == Match.id)
            .where(Prediction.result_spf != 0)
            .where(Match.kickoff_time >= W_START, Match.kickoff_time < W_END)
        )).all()

        match_ids = [p.match_id for p, _ in rows]
        snap_rows = (await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id.in_(match_ids))
        )).scalars().all()

        kickoff = {p.match_id: m.kickoff_time for p, m in rows}
        by_match = defaultdict(list)
        for s in snap_rows:
            kt = kickoff.get(s.match_id)
            if kt and s.snapshot_time < kt:
                by_match[s.match_id].append(s)
        last_snap = {}
        for mid, snaps in by_match.items():
            snaps.sort(key=lambda s: s.snapshot_time)
            last_time = snaps[-1].snapshot_time
            tail = [s for s in snaps if s.snapshot_time == last_time]
            h = np.mean([s.home_win for s in tail if s.home_win])
            d = np.mean([s.draw for s in tail if s.draw])
            a = np.mean([s.away_win for s in tail if s.away_win])
            last_snap[mid] = (h, d, a, last_time)

        samples = []
        for pred, m in rows:
            if pred.actual_home_score is None or pred.actual_away_score is None:
                continue
            if not (pred.home_prob and pred.draw_prob and pred.away_prob):
                continue
            actual = 0 if pred.actual_home_score > pred.actual_away_score else (
                1 if pred.actual_home_score == pred.actual_away_score else 2)
            probs = [pred.home_prob, pred.draw_prob, pred.away_prob]
            model_dir = int(np.argmax(probs))
            snap = last_snap.get(pred.match_id)
            if not snap:
                continue
            imp = implied_from_odds(*snap[:3])
            if not imp:
                continue
            market_fav = int(np.argmax(imp))
            samples.append({
                "match_id": pred.match_id,
                "kickoff": m.kickoff_time,
                "model_version": pred.model_version,
                "league": m.league.name_zh if m.league else "?",
                "home": m.home_team.name_zh if m.home_team else (m.home_team_name or ""),
                "away": m.away_team.name_zh if m.away_team else (m.away_team_name or ""),
                "actual": actual, "model_dir": model_dir, "market_fav": market_fav,
                "probs": probs, "imp": imp,
                "actual_hs": pred.actual_home_score, "actual_as": pred.actual_away_score,
            })
        for s in samples:
            fav = s["market_fav"]
            s["cold_gap"] = s["imp"][fav] - s["probs"][fav]
            s["cold_ok"] = 1 if s["market_fav"] != s["actual"] else 0

        N = len(samples)
        log(f"近30天已结算 {len(rows)} → 有效样本 {N}（排除概率缺失/无比分/无赛前快照）")
        seg_small = [s for s in samples if s["match_id"] < 1000]
        seg_main = [s for s in samples if s["match_id"] >= 15000]
        log(f"  id<1000（旧链路补跑）: {len(seg_small)} | id>=15000（当前链路）: {len(seg_main)}")
        vers = defaultdict(int)
        for s in samples:
            vers[s["model_version"]] += 1
        log(f"  model_version: {dict(vers)}")

        report_group("全样本", samples)
        report_group("id>=15000 当前链路", seg_main)
        report_group("id<1000 旧链路", seg_small)

        log(f"\n===== 分歧场明细（id>=15000, cold_gap 降序 top 30）=====")
        log(f"{'mid':>6} {'联赛':<5} {'对阵':<26} {'比分':<5} {'模→热':<7} {'gap':>5} {'实际':<4} {'ver':<14}")
        for s in sorted([x for x in seg_main if x["model_dir"] != x["market_fav"]], key=lambda x: -x["cold_gap"])[:30]:
            log(f"{s['match_id']:>6} {s['league'][:4]:<5} {(s['home']+'v'+s['away'])[:24]:<26} "
                f"{s['actual_hs']}:{s['actual_as']:<4} {DIRS[s['model_dir']]}→{DIRS[s['market_fav']]:<4} "
                f"{s['cold_gap']:>5.2f} {DIRS[s['actual']]:<4} {s['model_version']:<14}")

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        log(f"\n报告已写入 {REPORT_PATH}")

asyncio.run(main())
