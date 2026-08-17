"""搏冷因素 V2 探针：围绕「定价错误来源」验证四个方向

用户确认的方向：
  1. 市场高估偏差：市场隐含概率 vs 客观实力（排名/近况）的冲突程度
  2. 核心伤停/停赛：热门方缺阵 vs 冷门
  3. 赔率异常波动：资金异动（赔率涨跌幅过大）
  4. 大热必死/声望：知名球队被高估

口径与 _cold_factor_more.py 一致：近30天当前链路(id>=15000)已结算 + 赛前快照
市场特征严格 snapshot_time < kickoff_time；基本面 date < kickoff（point-in-time）
伤停/排名是"当前快照"，非 point-in-time，故另报 kickoff 距今 <=14 天子样本对照
输出：_out_cold_factor_v2.txt
"""
import asyncio, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot, TeamSeasonStats, HeadToHead, Injury
from scipy.stats import fisher_exact

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_cold_factor_v2.txt")
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

def parse_recent(m, ref_date):
    """从 recent_matches 取 date<ref_date 的场次；返回 (date, result, gf, ga, is_home)"""
    out = []
    for x in m or []:
        d = x.get("date")
        if not d:
            continue
        try:
            md = datetime.strptime(str(d)[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if md >= ref_date:
            continue
        r = (x.get("result") or "").upper()
        sc = str(x.get("score") or "")
        gf = ga = None
        if ":" in sc:
            try:
                g1, g2 = sc.split(":")
                gf, ga = int(g1), int(g2)
            except ValueError:
                pass
        is_home = x.get("is_home")
        if is_home is None:
            is_home = str(x.get("venue") or "").upper() == "H"
        out.append((md, r, gf, ga, bool(is_home)))
    out.sort(key=lambda t: t[0])
    return out

def last_n_form(recs, k=5):
    """近 k 场积分"""
    pts, n = 0.0, 0
    for md, r, g1, g2, is_h in recs:
        if r in ("W", "D", "L"):
            pts += 3 if r == "W" else (1 if r == "D" else 0)
            n += 1
        if n >= k:
            break
    return pts

def bucket_fisher(samples, key_fn, name, split_label):
    """按 bool 分两组比较冷门率，输出 Fisher p"""
    grp_true = [s for s in samples if key_fn(s)]
    grp_false = [s for s in samples if not key_fn(s)]
    t_c = sum(s["cold"] for s in grp_true)
    f_c = sum(s["cold"] for s in grp_false)
    t_n, f_n = len(grp_true), len(grp_false)
    if not t_n or not f_n:
        log(f"  {name}: 单组为空 (T={t_n} F={f_n})")
        return
    rate_t, rate_f = t_c/t_n, f_c/f_n
    try:
        p = fisher_exact([[t_c, t_n-t_c], [f_c, f_n-f_c]], alternative="two-sided")[1]
    except Exception:
        p = float("nan")
    log(f"  {name}: {split_label}=True {t_c}/{t_n}={rate_t:.1%} | False {f_c}/{f_n}={rate_f:.1%} | 基线对比 p={p:.3f}")

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
        leagues = {p.match_id: (m.league.name_zh if m.league else "?") for p, m in rows}
        league_ids = {p.match_id: m.league_id for p, m in rows}

        # 赛前快照
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

        # 排名：TeamSeasonStats 取每队最新非空 position（含联赛过滤）
        stat_rows = (await db.execute(
            select(TeamSeasonStats).where(TeamSeasonStats.team_id.in_(all_tids))
        )).scalars().all()
        rank_by_team = {}
        for st in stat_rows:
            if st.league_position is not None:
                key = st.team_id
                if key not in rank_by_team or (st.league_id and not rank_by_team[key].get("league_id")):
                    rank_by_team[key] = {"position": st.league_position, "points": st.league_points, "league_id": st.league_id}

        # 伤停：按 team 聚合（当前快照，非 point-in-time）
        inj_rows = (await db.execute(
            select(Injury).where(Injury.team_id.in_(all_tids), Injury.status == "out")
        )).scalars().all()
        inj_by_team = defaultdict(int)
        for it in inj_rows:
            inj_by_team[it.team_id] += 1

        # 声望代理：全历史 matches 中球队出现次数（match_id < 15000 历史 + 当前）
        hist_cnt = {}
        m_rows = (await db.execute(
            select(Match.home_team_id, Match.away_team_id)
            .where(Match.home_team_id.in_(all_tids) | Match.away_team_id.in_(all_tids))
        )).all()
        for h, a in m_rows:
            if h: hist_cnt[h] = hist_cnt.get(h, 0) + 1
            if a: hist_cnt[a] = hist_cnt.get(a, 0) + 1

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

            # ── 赔率异常波动：开盘→收盘各方向概率变动幅度 ──
            # 资金异动 = 概率变动异常大（非正常市场收敛）
            deltas = [imp_last[i] - imp_open[i] for i in range(3)]
            max_abs_delta = max(abs(d) for d in deltas)
            # 相邻时刻最大跳变（快照级异动）
            max_step = 0.0
            for i in range(1, len(times)):
                imp_a = consensus(times[i-1])
                imp_b = consensus(times[i])
                if imp_a and imp_b:
                    step = max(abs(imp_b[j] - imp_a[j]) for j in range(3))
                    max_step = max(max_step, step)

            # ── 市场高估偏差：热门方是否被排名/近况打脸 ──
            htid, atid = teams.get(mid, (None, None))
            rid = league_ids.get(mid)
            rk_h = rank_by_team.get(htid) if htid else None
            rk_a = rank_by_team.get(atid) if atid else None
            same_league_rank = bool(rk_h and rk_a and rk_h.get("league_id") == rk_a.get("league_id") and rk_h.get("league_id"))
            rank_gap = None  # 主队position - 客队position（负=主队排名更靠前）
            if rk_h and rk_a:
                rank_gap = rk_h["position"] - rk_a["position"]

            def best_recs(tid):
                if not tid:
                    return []
                best, best_n = [], -1
                for s in (x for x in stat_rows if x.team_id == tid):
                    recs = parse_recent(s.recent_matches, kt)
                    if len(recs) > best_n:
                        best_n, best = len(recs), recs
                return best
            rec_h = best_recs(htid)
            rec_a = best_recs(atid)
            form_h = last_n_form(rec_h)
            form_a = last_n_form(rec_a)
            form_gap = (form_h - form_a) if rec_h and rec_a else None  # 正=主队近况好

            # 市场热门 vs 客观实力方向冲突（仅热门为主/客时有效；平局为热门无意义）
            rank_fav_home = (fav_last == 0)
            rank_support = None
            if fav_last in (0, 2) and rank_gap is not None and same_league_rank:
                # rank_gap<0 表示主队排名靠前（position 更小）
                rank_support = rank_fav_home == (rank_gap < 0)
            form_support = None
            if fav_last in (0, 2) and form_gap is not None:
                form_support = rank_fav_home == (form_gap > 0)

            # 伤停（当前快照）
            inj_h = inj_by_team.get(htid, 0) if htid else 0
            inj_a = inj_by_team.get(atid, 0) if atid else 0
            inj_hot = inj_h if fav_last == 0 else (inj_a if fav_last == 2 else 0)
            inj_other = inj_a if fav_last == 0 else (inj_h if fav_last == 2 else 0)

            # 声望：热门方历史出现次数
            hist_hot = hist_cnt.get(htid if fav_last == 0 else atid, 0) if fav_last in (0, 2) else 0

            days_to_now = (NOW - kt.replace(tzinfo=None)).total_seconds() / 86400.0

            samples.append({
                "match_id": mid, "kickoff": kt, "league": leagues.get(mid, "?"),
                "home": m.home_team.name_zh if m.home_team else (m.home_team_name or ""),
                "away": m.away_team.name_zh if m.away_team else (m.away_team_name or ""),
                "actual": actual, "fav_last": fav_last, "cold": cold,
                "fav_prob": imp_last[fav_last],
                "max_abs_delta": max_abs_delta, "max_step": max_step,
                "rank_gap": rank_gap, "rank_support": rank_support, "same_league_rank": same_league_rank,
                "form_gap": form_gap, "form_support": form_support,
                "inj_h": inj_h, "inj_a": inj_a, "inj_hot": inj_hot, "inj_other": inj_other,
                "hist_hot": hist_hot, "days_to_now": days_to_now,
            })

        N = len(samples)
        base = sum(s["cold"] for s in samples) / N
        log(f"近30天当前链路有效样本: {N}，基线冷门率: {base:.1%}")
        log("")

        # 0) 特征覆盖统计
        n_rank = sum(1 for s in samples if s["rank_gap"] is not None and s["same_league_rank"])
        n_inj_any = sum(1 for s in samples if (s["inj_h"] + s["inj_a"]) > 0)
        n_inj_hot = sum(1 for s in samples if s["inj_hot"] > 0)
        log(f"[覆盖] 同联赛排名可用 {n_rank}/{N}；任一方有伤停 {n_inj_any}；热门方有伤停 {n_inj_hot}")
        log("")

        # ═══ 方向1：市场高估偏差 ═══
        log("===== 方向1：市场高估偏差（市场热门 vs 客观实力方向冲突） =====")
        log("  冷门定义：市场热门方向未命中。冲突 = 市场热门方向与排名/近况优势方向相反。")
        sub_r = [s for s in samples if s["same_league_rank"] and s["rank_gap"] is not None]
        sub_r_c = sum(s["cold"] for s in sub_r) / len(sub_r)
        log(f"  同联赛排名子样本 n={len(sub_r)}，基线冷门率 {sub_r_c:.1%}")
        bucket_fisher(sub_r, lambda s: s["rank_support"] is False, "  排名冲突(市场热门 vs 排名优势相反)", "冲突")
        bucket_fisher(sub_r, lambda s: s["rank_support"] is True, "  排名一致(市场热门 vs 排名优势同向)", "一致")
        # 排名差大且冲突
        big = [s for s in sub_r if s["rank_gap"] is not None and abs(s["rank_gap"]) >= 3]
        log(f"  排名差>=3 子样本 n={len(big)}，冷门率 {sum(s['cold'] for s in big)/len(big):.1%}" if big else "  排名差>=3 子样本为空")
        if big:
            bucket_fisher(big, lambda s: s["rank_support"] is False, "    其中排名冲突", "冲突")
        log("")

        sub_f = [s for s in samples if s["form_gap"] is not None]
        log(f"  近况可用子样本 n={len(sub_f)}，基线冷门率 {sum(s['cold'] for s in sub_f)/len(sub_f):.1%}")
        bucket_fisher(sub_f, lambda s: s["form_support"] is False, "  近况冲突(市场热门 vs 近况优势相反)", "冲突")
        bucket_fisher(sub_f, lambda s: s["form_support"] is True, "  近况一致(市场热门 vs 近况优势同向)", "一致")
        # 双冲突
        both = [s for s in samples if s["rank_support"] is False and s["form_support"] is False]
        if both:
            log(f"  排名+近况双冲突 n={len(both)}，冷门率 {sum(s['cold'] for s in both)/len(both):.1%}")
        else:
            log("  排名+近况双冲突样本为空")
        log("")

        # ═══ 方向2：核心伤停 ═══
        log("===== 方向2：热门方伤停/停赛 =====")
        log("  ⚠ 伤停为当前快照，非 point-in-time：先看全量，再看 kickoff 距今<=14天 子样本")
        bucket_fisher(samples, lambda s: s["inj_hot"] > 0, "  热门方缺阵>=1人", "缺阵")
        bucket_fisher(samples, lambda s: s["inj_hot"] >= 3, "  热门方缺阵>=3人", "缺阵3+")
        bucket_fisher(samples, lambda s: s["inj_hot"] >= s["inj_other"] + 2, "  热门缺阵 比 对方多>=2人", "热多缺")
        recent14 = [s for s in samples if s["days_to_now"] <= 14]
        log(f"  -- kickoff 距今<=14天 子样本 n={len(recent14)}，基线 {sum(s['cold'] for s in recent14)/len(recent14):.1%}")
        bucket_fisher(recent14, lambda s: s["inj_hot"] > 0, "    热门方缺阵>=1人", "缺阵")
        bucket_fisher(recent14, lambda s: s["inj_hot"] >= 3, "    热门方缺阵>=3人", "缺阵3+")
        log("")

        # ═══ 方向3：赔率异常波动 ═══
        log("===== 方向3：赔率异常波动（资金异动） =====")
        delta_vals = np.array([s["max_abs_delta"] for s in samples])
        p75 = np.percentile(delta_vals, 75)
        p90 = np.percentile(delta_vals, 90)
        log(f"  max_abs_delta 分布: p50={np.percentile(delta_vals,50):.3f} p75={p75:.3f} p90={p90:.3f}")
        bucket_fisher(samples, lambda s: s["max_abs_delta"] > p75, "  概率变动>p75(资金大动)", "异动")
        bucket_fisher(samples, lambda s: s["max_abs_delta"] > p90, "  概率变动>p90(资金剧烈异动)", "剧烈")
        step_vals = np.array([s["max_step"] for s in samples])
        sp75 = np.percentile(step_vals, 75)
        bucket_fisher(samples, lambda s: s["max_step"] > sp75, "  单步跳变>p75(盘中异动)", "跳变")
        # 连续分桶
        buckets = [(0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, None)]
        log("  max_abs_delta 分桶:")
        for lo, hi in buckets:
            grp = [s for s in samples if s["max_abs_delta"] >= lo and (hi is None or s["max_abs_delta"] < hi)]
            if grp:
                log(f"    [{lo:.2f},{hi if hi else '∞'}): n={len(grp)} 冷门率 {sum(s['cold'] for s in grp)/len(grp):.1%}")
        log("")

        # ═══ 方向4：大热必死/声望 ═══
        log("===== 方向4：大热必死/声望代理 =====")
        log("  声望代理=该队在竞彩历史(match_id<15000+当前)出现次数；检验热门方知名度 vs 冷门率")
        hist_vals = np.array([s["hist_hot"] for s in samples if s["hist_hot"] > 0])
        if len(hist_vals):
            hp50 = np.median(hist_vals)
            hp75 = np.percentile(hist_vals, 75)
            log(f"  热门方历史出现次数分布: p50={hp50:.0f} p75={hp75:.0f} (n={len(hist_vals)})")
            bucket_fisher(samples, lambda s: s["hist_hot"] > hp75, "  热门方为高知名度(>p75)", "高知名度")
            # 结合 fav_prob：强热门高知名度
            strong = [s for s in samples if s["fav_prob"] >= 0.55]
            if strong:
                log(f"  -- 强热门(fav_prob>=0.55) 子样本 n={len(strong)}，冷门率 {sum(s['cold'] for s in strong)/len(strong):.1%}")
                bucket_fisher(strong, lambda s: s["hist_hot"] > hp75, "    其中高知名度", "高知名度")
        else:
            log("  声望代理样本为空")
        log("")

        # 明细输出：强信号样本
        log("===== 高信号样本明细（供人工核验） =====")
        strong_sig = [s for s in samples if (s["rank_support"] is False and s["form_support"] is False) or s["inj_hot"] >= 3 or s["max_abs_delta"] > p90]
        for s in sorted(strong_sig, key=lambda x: x["kickoff"])[:25]:
            log(f"  [{s['kickoff']:%m-%d %H:%M}] {s['home']} vs {s['away']} | 热门={DIRS[s['fav_last']]}({s['fav_prob']:.2f}) | "
                f"实际={DIRS[s['actual']]} | 冷门={'是' if s['cold'] else '否'} | "
                f"rank_gap={s['rank_gap']} 近况Gap={s['form_gap'] if s['form_gap'] is not None else 'NA'} | "
                f"热门缺阵={s['inj_hot']} 对方缺阵={s['inj_other']} | Δmax={s['max_abs_delta']:.3f}")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))

asyncio.run(main())
