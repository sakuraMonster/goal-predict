"""方向1（排名冲突）稳健性验证

嫌疑点：
  1. 排名是「当前快照」（08-15 首次采集），对 07-16~08-15 的比赛有 look-ahead
  2. 是否只是 fav_prob 的代理（弱热门不显著，需排除）
  3. 随机标签对照：真实标签 p=0.001 是否落在噪声区间
  4. 反向选择模拟：若冲突→选排名优势方，命中率如何

验证方法：
  A. 随机标签对照：shuffle cold 标签 30 次，重算 rank_conflict Fisher p 分布
  B. fav_prob 控制：冲突 vs 非冲突 的 fav_prob 均值/分布
  C. 时间子样本：kickoff 距今 <=14d vs >14d（近期排名更接近赛时）
  D. 联赛分布：冲突样本联赛构成（排除单联赛主导）
  E. 反向选择命中率：冲突场次选「排名优势方」作为预测方向的命中率

口径与 _cold_factor_v2.py 完全一致。
"""
import asyncio, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot, TeamSeasonStats, Injury
from scipy.stats import fisher_exact

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_cold_factor_v2_robust.txt")
OUT = []
def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

DIRS = {0: "主胜", 1: "平局", 2: "客胜"}
W_START = datetime(2026, 7, 16, 12, 0)
W_END = datetime(2026, 8, 15, 12, 0)
NOW = datetime.utcnow()

def implied_from_odds(h, d, a):
    if not h or not d or not a or min(h, d, a) <= 1.01:
        return None
    ih, id_, ia = 1/h, 1/d, 1/a
    tot = ih + id_ + ia
    return ih/tot, id_/tot, ia/tot

async def main():
    async with async_session() as db:
        rows = (await db.execute(
            select(Prediction, Match).join(Match, Prediction.match_id == Match.id)
            .where(Prediction.result_spf != 0)
            .where(Match.kickoff_time >= W_START, Match.kickoff_time < W_END)
        )).all()
        match_ids = [p.match_id for p, _ in rows]
        kickoff = {p.match_id: m.kickoff_time for p, m in rows}
        teams = {p.match_id: (m.home_team_id, m.away_team_id) for p, m in rows}
        league_names = {p.match_id: (m.league.name_zh if m.league else "?") for p, m in rows}

        snaps = (await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id.in_(match_ids))
        )).scalars().all()
        by_match = defaultdict(list)
        for s in snaps:
            kt = kickoff.get(s.match_id)
            if kt and s.snapshot_time < kt:
                by_match[s.match_id].append(s)

        all_tids = set()
        for h, a in teams.values():
            if h: all_tids.add(h)
            if a: all_tids.add(a)

        stat_rows = (await db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.team_id.in_(all_tids))
        )).scalars().all()
        rank_by_team = {}
        for st in stat_rows:
            if st.league_position is not None:
                key = st.team_id
                if key not in rank_by_team or (st.league_id and not rank_by_team[key].get("league_id")):
                    rank_by_team[key] = {"position": st.league_position, "league_id": st.league_id}

        samples = []
        for pred, m in rows:
            if pred.actual_home_score is None or pred.actual_away_score is None or pred.match_id < 15000:
                continue
            mid = pred.match_id
            kt = kickoff[mid]
            actual = 0 if pred.actual_home_score > pred.actual_away_score else (
                1 if pred.actual_home_score == pred.actual_away_score else 2)
            snap_list = by_match.get(mid)
            if not snap_list:
                continue
            by_time = defaultdict(list)
            for s in snap_list:
                by_time[s.snapshot_time].append(s)
            times = sorted(by_time.keys())
            if len(times) < 2:
                continue

            def consensus(t):
                spf = {}
                for s in by_time[t]:
                    bm = s.bookmaker or "unknown"
                    if bm not in spf and s.home_win and s.draw and s.away_win:
                        spf[bm] = s
                if not spf:
                    return None
                return implied_from_odds(
                    np.mean([s.home_win for s in spf.values()]),
                    np.mean([s.draw for s in spf.values()]),
                    np.mean([s.away_win for s in spf.values()]))
            op_time = next((t for t in times if any(s.is_opening for s in by_time[t])), times[0])
            imp_open = consensus(op_time)
            imp_last = consensus(times[-1])
            if not imp_open or not imp_last:
                continue
            fav_last = int(np.argmax(imp_last))
            cold = 1 if fav_last != actual else 0

            htid, atid = teams.get(mid, (None, None))
            rk_h = rank_by_team.get(htid) if htid else None
            rk_a = rank_by_team.get(atid) if atid else None
            same_league = bool(rk_h and rk_a and rk_h.get("league_id") == rk_a.get("league_id") and rk_h.get("league_id"))
            rank_gap = (rk_h["position"] - rk_a["position"]) if (rk_h and rk_a) else None
            rank_support = None
            if fav_last in (0, 2) and rank_gap is not None and same_league:
                rank_support = (fav_last == 0) == (rank_gap < 0)

            days_to_now = (NOW - kt.replace(tzinfo=None)).total_seconds() / 86400.0
            samples.append({
                "match_id": mid, "kickoff": kt, "league": league_names.get(mid, "?"),
                "home": m.home_team.name_zh if m.home_team else (m.home_team_name or ""),
                "away": m.away_team.name_zh if m.away_team else (m.away_team_name or ""),
                "actual": actual, "fav_last": fav_last, "cold": cold,
                "fav_prob": imp_last[fav_last],
                "rank_gap": rank_gap, "rank_support": rank_support, "same_league_rank": same_league,
                "days_to_now": days_to_now,
            })

        sub = [s for s in samples if s["rank_support"] is not None]
        N = len(sub)
        confl = [s for s in sub if s["rank_support"] is False]
        nonc = [s for s in sub if s["rank_support"] is True]
        c_c = sum(s["cold"] for s in confl); nc_c = sum(s["cold"] for s in nonc)
        log(f"同联赛排名子样本 n={N}；冲突 n={len(confl)} 冷门率 {c_c/len(confl):.1%}；非冲突 n={len(nonc)} 冷门率 {nc_c/len(nonc):.1%}")
        p = fisher_exact([[c_c, len(confl)-c_c], [nc_c, len(nonc)-nc_c]], alternative="two-sided")[1]
        log(f"Fisher p={p:.4f}")
        log("")

        # A. 随机标签对照（30 次 shuffle）
        rng = np.random.default_rng(42)
        p_shuff, rate_diff_shuff = [], []
        colds = np.array([s["cold"] for s in sub])
        confl_flags = np.array([s["rank_support"] is False for s in sub])
        for _ in range(30):
            sh = rng.permutation(colds)
            tc = int(sh[confl_flags].sum()); nc = int(sh[~confl_flags].sum())
            tn, nn = int(confl_flags.sum()), int((~confl_flags).sum())
            p_shuff.append(fisher_exact([[tc, tn-tc], [nc, nn-nc]], alternative="two-sided")[1])
            rate_diff_shuff.append(tc/tn - nc/nn)
        p_shuff = np.array(p_shuff)
        log("A. 随机标签对照（30 次 shuffle 冷门标签）:")
        log(f"  真实 p={p:.4f}；shuffle p 分布: min={p_shuff.min():.4f} p50={np.median(p_shuff):.4f} max={p_shuff.max():.4f}")
        log(f"  真实冷门率差={c_c/len(confl)-nc_c/len(nonc):.3f}；shuffle 率差分布: p50={np.median(rate_diff_shuff):.3f} max={np.max(rate_diff_shuff):.3f}")
        log(f"  真实 p 处于 shuffle 分布: {sum(p_shuff <= p)/len(p_shuff):.0%} 分位（<5% 才算稳健显著）")
        log("")

        # B. fav_prob 控制
        fp_c = np.array([s["fav_prob"] for s in confl]); fp_n = np.array([s["fav_prob"] for s in nonc])
        log(f"B. fav_prob 控制:")
        log(f"  冲突 fav_prob: 均值 {fp_c.mean():.3f} p50 {np.median(fp_c):.3f} | 非冲突: 均值 {fp_n.mean():.3f} p50 {np.median(fp_n):.3f}")
        # 弱热门代理检查：冲突样本里 fav_prob 是否都 <=0.55
        log(f"  冲突样本 fav_prob>=0.55 的场次: {sum(fp_c>=0.55)}/{len(fp_c)}（若过多则为弱热门代理）")
        # 控制 fav_prob 后分层
        for lo, hi in [(0.0, 0.45), (0.45, 0.55), (0.55, 1.01)]:
            g = [s for s in sub if lo <= s["fav_prob"] < hi]
            if not g: continue
            gc = [s for s in g if s["rank_support"] is False]
            if not gc: continue
            gn = [s for s in g if s["rank_support"] is True]
            r1 = sum(s["cold"] for s in gc)/len(gc); r2 = sum(s["cold"] for s in gn)/len(gn) if gn else float("nan")
            log(f"    fav_prob[{lo:.2f},{hi:.2f}): 冲突 {len(gc)}场 冷门率 {r1:.1%} vs 非冲突 {len(gn)}场 冷门率 {r2:.1%}")
        log("")

        # C. 时间子样本
        log(f"C. 时间子样本（rank 是当前快照，近期比赛更接近赛时排名）:")
        for label, cond in [("kickoff<=14d", lambda s: s["days_to_now"] <= 14),
                            ("kickoff>14d", lambda s: s["days_to_now"] > 14)]:
            g = [s for s in sub if cond(s)]
            gc = [s for s in g if s["rank_support"] is False]
            gn = [s for s in g if s["rank_support"] is True]
            if not gc or not gn: 
                log(f"  {label}: 样本不足 (冲突{len(gc)} 非冲突{len(gn)})")
                continue
            r1 = sum(s["cold"] for s in gc)/len(gc); r2 = sum(s["cold"] for s in gn)/len(gn)
            pp = fisher_exact([[sum(s['cold'] for s in gc), len(gc)-sum(s['cold'] for s in gc)],
                               [sum(s['cold'] for s in gn), len(gn)-sum(s['cold'] for s in gn)]], alternative="two-sided")[1]
            log(f"  {label}: 冲突 {len(gc)}场 {r1:.1%} vs 非冲突 {len(gn)}场 {r2:.1%} | p={pp:.3f}")
        log("")

        # D. 联赛分布
        log(f"D. 冲突样本联赛分布:")
        by_league = defaultdict(list)
        for s in confl:
            by_league[s["league"]].append(s)
        for lg, ss in sorted(by_league.items(), key=lambda x: -len(x[1])):
            log(f"  {lg}: {len(ss)}场 冷门率 {sum(s['cold'] for s in ss)/len(ss):.1%}")
        log("")

        # E. 反向选择模拟：冲突场次选「排名优势方」的命中率
        log("E. 反向选择模拟（冲突场次：市场方向 vs 排名优势方向 vs 实际）:")
        hit_market = 0; hit_rank = 0; tot = 0
        for s in confl:
            # 排名优势方：rank_gap<0 → 主队排名靠前（position 更小）
            if s["fav_last"] == 0:
                rank_fav_dir = 0 if (s["rank_gap"] or 0) < 0 else 2
            else:
                rank_fav_dir = 2 if (s["rank_gap"] or 0) < 0 else 0
            hit_market += (s["fav_last"] == s["actual"])
            hit_rank += (rank_fav_dir == s["actual"])
            tot += 1
        log(f"  冲突场次 n={tot}：市场方向命中 {hit_market} ({hit_market/tot:.1%}) | 排名优势方向命中 {hit_rank} ({hit_rank/tot:.1%})")
        # 非冲突场次对照组
        hit_rank_nc = 0; tot_nc = 0
        for s in nonc:
            if s["fav_last"] == 0:
                rank_fav_dir = 0 if (s["rank_gap"] or 0) < 0 else 2
            else:
                rank_fav_dir = 2 if (s["rank_gap"] or 0) < 0 else 0
            hit_rank_nc += (rank_fav_dir == s["actual"])
            tot_nc += 1
        log(f"  非冲突场次 n={tot_nc}：排名优势方向命中 {hit_rank_nc} ({hit_rank_nc/tot_nc:.1%})")
        log("")

        # F. 冲突样本明细
        log("F. 冲突样本全部明细:")
        for s in sorted(confl, key=lambda x: x["kickoff"]):
            fav_name = DIRS[s["fav_last"]]
            rank_better = "主" if (s["rank_gap"] or 0) < 0 else "客"
            log(f"  [{s['kickoff']:%m-%d %H:%M}] {s['home']} vs {s['away']} | 市场热门={fav_name}({s['fav_prob']:.2f}) 排名优势={rank_better}(gap={s['rank_gap']}) | 实际={DIRS[s['actual']]} | {'冷' if s['cold'] else '非冷'}")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))

asyncio.run(main())
