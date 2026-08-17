"""独立博冷模型：稳健性检查
对 id>=15000 当前链路样本做：
1. 多种切分比例 (50/50, 60/40, 70/30) × 3 个 LGB 种子，看测试 AUC/top-k 是否稳定
2. LGB 特征重要性
3. 随机标签对照（验证 AUC 不是结构假象）
输出：_out_cold_model_robust.txt
"""
import asyncio, os, sys, json
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot, TeamSeasonStats, HeadToHead
from sklearn.metrics import roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_cold_model_robust.txt")
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
                continue  # 仅当前链路
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
                "match_id": mid, "kickoff": kt,
                "actual": actual, "fav_last": fav_last, "cold": cold,
                "fav_prob": imp_last[fav_last],
                "fav_prob_open": imp_open[fav_open],
                "fav_drift": imp_last[fav_last] - imp_open[fav_open],
                "drift_h": drift[0], "drift_d": drift[1], "drift_a": drift[2],
                "max_abs_drift": max(abs(x) for x in drift),
                "disp": disp,
                "gl_shift": (gl_last - gl_open) if (gl_open is not None and gl_last is not None) else np.nan,
                "hcp_shift": (hcp_last - hcp_open) if (hcp_open is not None and hcp_last is not None) else np.nan,
                "pre_hours": (kt - times[0]).total_seconds() / 3600,
                "last_gap_hours": (kt - last_time).total_seconds() / 3600,
                "n_snap_times": len(times),
                "form_h": fh[0], "form_a": fa[0], "gf_h": fh[1], "ga_h": fh[2],
                "gf_a": fa[1], "ga_a": fa[2],
                "h2h_pts": h2h_h, "h2h_n": h2h_n,
            })

        N = len(samples)
        log(f"当前链路样本: {N}, 基线冷门率 {sum(s['cold'] for s in samples)/N:.1%}")
        FEATS = [k for k in samples[0] if k not in ("match_id", "kickoff", "actual", "fav_last", "cold")]
        log(f"特征数: {len(FEATS)}: {', '.join(FEATS)}")
        log("")

        def X_of(ds):
            X = []
            for s in ds:
                row = []
                for k in FEATS:
                    v = s[k]
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        v = 0.0
                    row.append(float(v))
                X.append(row)
            return np.array(X)

        samples.sort(key=lambda s: s["kickoff"])
        X_all = X_of(samples)
        y_all = np.array([s["cold"] for s in samples])

        # ── 1) 多切分 × 多种子 ──
        log("===== 多切分 × 多种子 (LR 与 LGB 测试 AUC) =====")
        splits = {"50/50": 0.5, "60/40": 0.6, "70/30": 0.7}
        seeds = [0, 42, 2026]
        summary = {sp: {"lr": [], "lgb": []} for sp in splits}
        for sp_name, frac in splits.items():
            n_tr = int(N * frac)
            Xtr, ytr = X_all[:n_tr], y_all[:n_tr]
            Xte, yte = X_all[n_tr:], y_all[n_tr:]
            te = samples[n_tr:]
            te_base = yte.mean()
            sc = StandardScaler().fit(Xtr)
            clf = LogisticRegression(max_iter=1000, C=1.0).fit(sc.transform(Xtr), ytr)
            auc_lr = roc_auc_score(yte, clf.predict_proba(sc.transform(Xte))[:, 1])
            log(f"\n[{sp_name}] 训练 {n_tr} / 测试 {N-n_tr}, 测试基线 {te_base:.1%}")
            log(f"  LR  AUC={auc_lr:.3f}")
            for seed in seeds:
                g = lgb.LGBMClassifier(n_estimators=40, max_depth=2, learning_rate=0.05,
                                       num_leaves=7, min_child_samples=8,
                                       reg_alpha=1.0, reg_lambda=1.0,
                                       random_state=seed, verbose=-1)
                g.fit(Xtr, ytr)
                pa = g.predict_proba(Xte)[:, 1]
                a = roc_auc_score(yte, pa)
                # top30% 冷门率
                order = np.argsort(-pa)
                k = max(1, int(len(te) * 0.3))
                r = sum(1 for i in order[:k] if yte[i]) / k
                summary[sp_name]["lgb"].append(a)
                log(f"  LGB seed={seed:<4} AUC={a:.3f} top30%冷门率={r:.1%}")
            summary[sp_name]["lr"].append(auc_lr)
        log("\nAUC 汇总 (mean±std):")
        for sp_name in splits:
            lr_m = np.mean(summary[sp_name]["lr"])
            lgb_v = summary[sp_name]["lgb"]
            log(f"  {sp_name}: LR {lr_m:.3f} | LGB {np.mean(lgb_v):.3f}±{np.std(lgb_v):.3f} "
                f"(范围 {min(lgb_v):.3f}~{max(lgb_v):.3f})")

        # ── 2) 随机标签对照（60/40 固定）──
        log("\n===== 随机标签对照 (60/40, 10 次 shuffle 标签的 LGB AUC 分布) =====")
        rng = np.random.default_rng(0)
        n_tr = int(N * 0.6)
        Xtr, Xte = X_all[:n_tr], X_all[n_tr:]
        rand_aucs = []
        for _ in range(10):
            y_shuf = rng.permutation(y_all)
            g = lgb.LGBMClassifier(n_estimators=40, max_depth=2, learning_rate=0.05,
                                   num_leaves=7, min_child_samples=8,
                                   reg_alpha=1.0, reg_lambda=1.0,
                                   random_state=42, verbose=-1)
            g.fit(Xtr, y_shuf[:n_tr])
            pa = g.predict_proba(Xte)[:, 1]
            try:
                rand_aucs.append(roc_auc_score(y_shuf[n_tr:], pa))
            except ValueError:
                pass
        log(f"  随机标签 LGB AUC: {['%.3f' % a for a in rand_aucs]}")
        log(f"  均值 {np.mean(rand_aucs):.3f}±{np.std(rand_aucs):.3f} (对比真实标签 AUC)")

        # ── 3) 特征重要性（60/40 平均）──
        log("\n===== LGB 特征重要性 (60/40, 3 种子平均) =====")
        imp_sum = np.zeros(len(FEATS))
        for seed in seeds:
            g = lgb.LGBMClassifier(n_estimators=40, max_depth=2, learning_rate=0.05,
                                   num_leaves=7, min_child_samples=8,
                                   reg_alpha=1.0, reg_lambda=1.0,
                                   random_state=seed, verbose=-1)
            g.fit(X_all[:n_tr], y_all[:n_tr])
            imp_sum += g.feature_importances_
        for i in np.argsort(-imp_sum):
            log(f"  {FEATS[i]:<15} {imp_sum[i]/len(seeds):.1f}")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT))
    print(f"\n[已写报告] {REPORT_PATH}")

asyncio.run(main())
