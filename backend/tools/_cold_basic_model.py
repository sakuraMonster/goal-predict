"""基础搏冷模型：基于近30天赛果反向归纳（用户口径：不要求样本外验证）

交付物：
1. 市场热门概率校准表（市场隐含概率 vs 实际热门胜率 —— 看市场是否高估热门）
2. 冷门 vs 非冷门场次的特征对比
3. 可解释搏冷规则集（每条规则 n / 冷门率 / Fisher 精确检验 p 值）
4. 基础评分模型（命中规则数）+ 近30天每日 top_n 模拟
5. 诚实局限说明

样本：近30天(07-16~08-15)当前链路(id>=15000)已结算 + 赛前有快照
口径：市场特征严格 snapshot_time < kickoff_time；基本面按 date < kickoff 过滤（point-in-time）
输出：_out_cold_basic_model.txt
"""
import asyncio, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot, TeamSeasonStats, HeadToHead
from scipy.stats import fisher_exact

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_cold_basic_model.txt")
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
    tot = ih + id_ + ia
    return ih/tot, id_/tot, ia/tot

def form_points(matches, ref_date, k=5):
    pts, gf, ga, n = 0.0, 0, 0, 0
    for m in matches or []:
        d = m.get("date")
        if not d:
            continue
        try:
            md = datetime.strptime(str(d)[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if md >= ref_date:
            continue
        r = (m.get("result") or "").upper()
        sc = m.get("score") or ""
        if r in ("W", "D", "L"):
            pts += 3 if r == "W" else (1 if r == "D" else 0)
            n += 1
        if ":" in sc:
            try:
                g1, g2 = sc.split(":")
                gf += int(g1); ga += int(g2)
            except ValueError:
                pass
        if n >= k:
            break
    return pts, gf, ga, n

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
        )).all()
        stats_by_team = defaultdict(list)
        for r in stat_rows:
            stats_by_team[r[0].team_id].append(r[0])
        h2h_rows = (await db.execute(
            select(HeadToHead).where(
                HeadToHead.home_team_id.in_(all_tids), HeadToHead.away_team_id.in_(all_tids))
        )).all()
        h2h_by_pair = defaultdict(list)
        for r in h2h_rows:
            h2h_by_pair[(r[0].home_team_id, r[0].away_team_id)].append(r[0])

        samples = []
        for pred, m in rows:
            if pred.actual_home_score is None or pred.actual_away_score is None:
                continue
            if pred.match_id < 15000:
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
            if len(times) < 1:
                continue

            def consensus(t):
                spf = {}
                for s in by_time[t]:
                    bm = s.bookmaker or "unknown"
                    if bm not in spf and s.home_win and s.draw and s.away_win:
                        spf[bm] = s
                if not spf:
                    return None, None
                hs = np.mean([s.home_win for s in spf.values()])
                ds = np.mean([s.draw for s in spf.values()])
                aw = np.mean([s.away_win for s in spf.values()])
                return implied_from_odds(hs, ds, aw), spf

            op_time = None
            for t in times:
                if any(s.is_opening for s in by_time[t]):
                    op_time = t
                    break
            if op_time is None:
                op_time = times[0]
            last_time = times[-1]
            imp_open, _ = consensus(op_time)
            imp_last, spf_last = consensus(last_time)
            if not imp_open or not imp_last:
                continue
            fav_last = int(np.argmax(imp_last))
            fav_open = int(np.argmax(imp_open))
            drift = [imp_last[i] - imp_open[i] for i in range(3)]

            if spf_last and len(spf_last) >= 2:
                st = {i: [] for i in range(3)}
                for bm, s in spf_last.items():
                    imp = implied_from_odds(s.home_win, s.draw, s.away_win)
                    if imp:
                        for i in range(3):
                            st[i].append(imp[i])
                disp = max(np.std(st[i]) for i in range(3) if st[i])
            else:
                disp = np.nan

            def main_line(t, kind):
                vals = [getattr(s, kind) for s in by_time[t] if getattr(s, kind) is not None]
                if not vals:
                    return None
                cnt = defaultdict(int)
                for v in vals:
                    cnt[round(v, 2)] += 1
                return max(cnt, key=cnt.get)

            gl_open = main_line(op_time, "goal_line")
            gl_last = main_line(last_time, "goal_line")
            hcp_open = main_line(op_time, "handicap_line")
            hcp_last = main_line(last_time, "handicap_line")

            htid, atid = teams.get(mid, (None, None))
            def team_form(tid):
                if not tid:
                    return (np.nan, np.nan, np.nan, 0)
                best, best_n = None, -1
                for s in stats_by_team.get(tid, []):
                    pts, gf, ga, n = form_points(s.recent_matches, kt)
                    if n > best_n:
                        best_n, best = n, (pts, gf, ga, n)
                return best if best else (np.nan, np.nan, np.nan, 0)

            fh = team_form(htid)
            fa = team_form(atid)

            def h2h_balance():
                hpts, n = 0.0, 0
                for (hh, ha), lst in h2h_by_pair.items():
                    if not (hh == htid and ha == atid) and not (hh == atid and ha == htid):
                        continue
                    for rec in lst:
                        if rec.match_date and rec.match_date >= kt:
                            continue
                        if rec.home_score is None or rec.away_score is None:
                            continue
                        rev = (hh == atid)
                        hs, as_ = (rec.away_score, rec.home_score) if rev else (rec.home_score, rec.away_score)
                        if hs > as_: hpts += 3
                        elif hs == as_: hpts += 1
                        n += 1
                return hpts, n
            h2h_h, h2h_n = h2h_balance()

            cold = 1 if fav_last != actual else 0
            samples.append({
                "match_id": mid, "kickoff": kt, "league": m.league.name_zh if m.league else "?",
                "home": m.home_team.name_zh if m.home_team else (m.home_team_name or ""),
                "away": m.away_team.name_zh if m.away_team else (m.away_team_name or ""),
                "actual": actual, "fav_last": fav_last, "actual_hs": pred.actual_home_score,
                "actual_as": pred.actual_away_score, "cold": cold,
                "fav_prob": imp_last[fav_last], "fav_prob_open": imp_open[fav_open],
                "fav_drift": imp_last[fav_last] - imp_open[fav_open],
                "drift_h": drift[0], "drift_d": drift[1], "drift_a": drift[2],
                "max_abs_drift": max(abs(x) for x in drift),
                "disp": disp,
                "gl_shift": (gl_last - gl_open) if (gl_open is not None and gl_last is not None) else np.nan,
                "hcp_shift": (hcp_last - hcp_open) if (hcp_open is not None and hcp_last is not None) else np.nan,
                "pre_hours": (kt - times[0]).total_seconds() / 3600,
                "form_h": fh[0], "form_a": fa[0], "gf_h": fh[1], "ga_h": fh[2],
                "gf_a": fa[1], "ga_a": fa[2],
                "h2h_pts": h2h_h, "h2h_n": h2h_n,
            })

        N = len(samples)
        base = sum(s["cold"] for s in samples) / N
        log(f"近30天当前链路有效样本: {N}，基线冷门率(市场热门失败): {base:.1%}")
        log("")

        # ── 1) 市场热门概率校准表 ──
        log("===== 1. 市场热门概率校准（市场隐含概率 vs 实际热门胜率） =====")
        log("（若 实际胜率 < 隐含概率 → 市场在该档高估热门 → 该档更可搏冷）")
        bins = [(0.30, 0.40), (0.40, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 0.65), (0.65, 1.0)]
        for lo, hi in bins:
            seg = [s for s in samples if lo <= s["fav_prob"] < hi]
            if len(seg) < 5:
                continue
            imp_mean = np.mean([s["fav_prob"] for s in seg])
            win = sum(1 for s in seg if s["actual"] == s["fav_last"])
            win_rate = win / len(seg)
            gap = win_rate - imp_mean
            log(f"  fav_prob {lo:.2f}~{hi:.2f}: n={len(seg):<3} 隐含热门均值 {imp_mean:.3f} "
                f"实际热门胜率 {win_rate:.1%} 差异 {gap:+.1%}")
        log("")

        # ── 2) 冷门 vs 非冷门 特征对比 ──
        log("===== 2. 冷门 vs 非冷门场次特征对比（均值） =====")
        colds = [s for s in samples if s["cold"]]
        hots = [s for s in samples if not s["cold"]]
        for key, desc in [
            ("fav_prob", "热门隐含概率(收)"), ("fav_prob_open", "热门隐含概率(开)"),
            ("fav_drift", "热门概率漂移"), ("disp", "bookmaker分歧度"),
            ("max_abs_drift", "最大绝对漂移"), ("hcp_shift", "亚盘移动"),
            ("form_h", "主队近5场积分"), ("form_a", "客队近5场积分"),
            ("h2h_pts", "h2h主队积分"), ("pre_hours", "赛前快照深度(h)"),
        ]:
            def mean_of(ds):
                v = [s[key] for s in ds if s[key] is not None and not (isinstance(s[key], float) and np.isnan(s[key]))]
                return np.mean(v) if v else float("nan")
            log(f"  {desc:<16} 冷门场 {mean_of(colds):>7.3f} | 非冷门场 {mean_of(hots):>7.3f}")
        fav_away_c = sum(1 for s in colds if s["fav_last"] == 2) / len(colds)
        fav_away_h = sum(1 for s in hots if s["fav_last"] == 2) / len(hots)
        log(f"  客队热门占比      冷门场 {fav_away_c:.1%} | 非冷门场 {fav_away_h:.1%}")
        log("")

        # ── 3) 搏冷规则集（经济直觉规则 + Fisher 精确检验）──
        log("===== 3. 可解释搏冷规则集（每条: n / 冷门率 / Fisher p 值） =====")
        RULES = [
            ("R1 弱热门 fav_prob<=0.45", lambda s: s["fav_prob"] <= 0.45),
            ("R2 超弱热门 fav_prob<=0.40", lambda s: s["fav_prob"] <= 0.40),
            ("R3 热门临场被抛 fav_drift<=-0.01", lambda s: s["fav_drift"] <= -0.01),
            ("R4 分歧大 disp>=0.03", lambda s: s["disp"] >= 0.03),
            ("R5 客队热门", lambda s: s["fav_last"] == 2),
            ("R6 市场剧烈波动 |drift|>=0.02", lambda s: s["max_abs_drift"] >= 0.02),
            ("R7 主队状态好于客队 form_h>form_a", lambda s: s["form_h"] > s["form_a"]),
            ("R8 客队热门且主队状态好", lambda s: s["fav_last"] == 2 and s["form_h"] > s["form_a"]),
            ("R9 弱热门且客队热门", lambda s: s["fav_prob"] <= 0.45 and s["fav_last"] == 2),
            ("R10 弱热门且热门被抛", lambda s: s["fav_prob"] <= 0.45 and s["fav_drift"] <= -0.01),
            ("R11 h2h主队占优(积分>=3)", lambda s: s["h2h_n"] >= 2 and s["h2h_pts"] >= 3),
            ("R12 弱热门且分歧大", lambda s: s["fav_prob"] <= 0.45 and s["disp"] >= 0.03),
        ]
        rule_results = []
        for name, fn in RULES:
            hits = [s for s in samples if fn(s)]
            if len(hits) < 8:
                rule_results.append((name, len(hits), float("nan"), float("nan"), fn))
                continue
            c = sum(1 for s in hits if s["cold"])
            r = c / len(hits)
            # Fisher 精确检验：规则命中 vs 未命中 的冷门率差异
            not_hits = [s for s in samples if not fn(s)]
            c0 = sum(1 for s in not_hits if s["cold"])
            table = [[c, len(hits) - c], [c0, len(not_hits) - c0]]
            _, p = fisher_exact(table)
            rule_results.append((name, len(hits), r, p, fn))
        for name, n, r, p, fn in sorted(rule_results, key=lambda x: (-x[2] if not np.isnan(x[2]) else 0)):
            if np.isnan(r):
                log(f"  {name:<30} n={n:<3} 样本不足")
            else:
                sig = " **" if p < 0.05 else (" *" if p < 0.10 else "")
                log(f"  {name:<30} n={n:<3} 冷门率 {r:.1%} (p={p:.3f}){sig}")
        log("")

        # ── 4) 基础评分模型：命中规则数 ──
        log("===== 4. 基础评分模型（命中规则数，仅用 n>=15 的规则） =====")
        used_rules = [fn for name, n, r, p, fn in rule_results if n >= 15 and not np.isnan(r)]
        for s in samples:
            s["score"] = sum(1 for fn in used_rules if fn(s))
        score_dist = defaultdict(list)
        for s in samples:
            score_dist[s["score"]].append(s)
        log("  各分值档冷门率:")
        for sc in sorted(score_dist):
            seg = score_dist[sc]
            r = sum(1 for s in seg if s["cold"]) / len(seg)
            log(f"    score={sc}: n={len(seg):<3} 冷门率 {r:.1%}")
        # 排序：score 降序，同分 fav_prob 升序（弱热门优先）
        ranked = sorted(samples, key=lambda s: (-s["score"], s["fav_prob"]))
        ranked_cold = [s["cold"] for s in ranked]
        for frac in (0.2, 0.3, 0.5):
            k = max(1, int(N * frac))
            r = sum(ranked_cold[:k]) / k
            log(f"  top{frac:.0%}(n={k}): 冷门率 {r:.1%} vs 基线 {base:.1%}")
        # 每日 top_n（近30天全样本模拟）
        day_of = defaultdict(list)
        for s in samples:
            kt = s["kickoff"]
            day = kt.date() if kt.hour >= 12 else (kt - timedelta(hours=12)).date()
            day_of[day].append(s)
        log("\n  每日 top2 / top3 模拟（近30天全部已结算场次，样本内归纳）:")
        for top_n in (2, 3):
            picks, hits = 0, 0
            for day, ds in sorted(day_of.items()):
                for s in sorted(ds, key=lambda x: (-x["score"], x["fav_prob"]))[:top_n]:
                    picks += 1
                    hits += s["cold"]
            log(f"    每日 top{top_n}: {hits}/{picks} = {hits/picks:.1%} (冷门率)" if picks else f"    每日 top{top_n}: 无")

        # ── 5) 每日 top3 明细 ──
        log("\n  每日 top3 明细（近30天）:")
        for day, ds in sorted(day_of.items()):
            top = sorted(ds, key=lambda x: (-x["score"], x["fav_prob"]))[:3]
            tags = "/".join("冷" if s["cold"] else "热" for s in top)
            desc = "; ".join(f"{s['home']}v{s['away']}({DIRS[s['fav_last']]})" for s in top)
            log(f"    {day}: {tags} | {desc}")

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        log(f"\n[已写报告] {REPORT_PATH}")

asyncio.run(main())
