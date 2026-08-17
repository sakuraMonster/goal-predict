"""独立博冷模型：特征分离探针（市场行为 + 基本面 → 冷门标签）

口径：
- 样本：近30天(07-16~08-15 比赛日)已结算预测，且赛前有快照
- 市场特征：仅用 snapshot_time < kickoff_time 的快照（严格防 look-ahead）
  - 隐含概率：1/odds 去水归一化；开=最早赛前快照时刻(或 is_opening)，收=最后一刻
- 基本面特征（point-in-time）：
  - recent_matches 仅取 date < kickoff 的记录算近5场状态/得失球（避免行级污染与未来泄漏）
  - h2h 仅取 match_date < kickoff
- 标签：cold = 市场热门(最后一刻隐含概率 argmax) 未赢
- 评估：单特征 AUC + 分桶冷门率；时间切分(训练=最早60%,测试=后40%)逻辑回归 vs 基线
输出：_out_cold_model.txt
"""
import asyncio, os, sys, json
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
sys.path.insert(0, r"e:\zhangxuejun\new-thinking\ricking-03\backend")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Prediction, Match, OddsSnapshot, TeamSeasonStats, HeadToHead

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_cold_model.txt")
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
    """从 recent_matches 取 date<ref_date 的最近 k 场，算积分(W=3,D=1)与得失球"""
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

        # ── 市场快照（严格赛前）──
        snaps = (await db.execute(
            select(OddsSnapshot).where(OddsSnapshot.match_id.in_(match_ids))
        )).scalars().all()
        by_match = defaultdict(list)
        for s in snaps:
            kt = kickoff.get(s.match_id)
            if kt and s.snapshot_time < kt:
                by_match[s.match_id].append(s)

        # ── 基本面：一次拉取涉及球队的 stats 与 h2h ──
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

        # ── 单场特征 ──
        samples = []
        for pred, m in rows:
            if pred.actual_home_score is None or pred.actual_away_score is None:
                continue
            mid = pred.match_id
            kt = kickoff[mid]
            actual = 0 if pred.actual_home_score > pred.actual_away_score else (
                1 if pred.actual_home_score == pred.actual_away_score else 2)

            snap_list = by_match.get(mid)
            if not snap_list:
                continue
            # 按快照时刻分组
            by_time = defaultdict(list)
            for s in snap_list:
                by_time[s.snapshot_time].append(s)
            times = sorted(by_time.keys())
            if len(times) < 1:
                continue

            def consensus(t):
                """该时刻全部 bookmaker 平均去水隐含概率（SPF 每 bookmaker 去重）"""
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
                imp = implied_from_odds(hs, ds, aw)
                return imp, spf

            # 开盘时刻：优先 is_opening 标记，否则最早时刻
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

            # 分散度（最后一刻，bookmaker 间 std）
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

            # 大小球/亚盘线移动（开→收，取该时刻众数线）
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

            # ── 基本面 ──
            htid, atid = teams.get(mid, (None, None))
            def team_form(tid):
                if not tid:
                    return (np.nan, np.nan, np.nan, 0)
                best = None
                best_n = -1
                for s in stats_by_team.get(tid, []):
                    pts, gf, ga, n = form_points(s.recent_matches, kt)
                    if n > best_n:
                        best_n = n
                        best = (pts, gf, ga, n)
                return best if best else (np.nan, np.nan, np.nan, 0)

            fh = team_form(htid)
            fa = team_form(atid)

            # h2h（双向汇总，取 date<kickoff）
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
                "actual": actual, "fav_last": fav_last, "fav_open": fav_open,
                "actual_hs": pred.actual_home_score, "actual_as": pred.actual_away_score,
                "cold": cold,
                # 市场特征
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
                # 基本面
                "form_h": fh[0], "form_a": fa[0], "gf_h": fh[1], "ga_h": fh[2], "gf_a": fa[1], "ga_a": fa[2],
                "h2h_pts": h2h_h, "h2h_n": h2h_n,
            })

        N = len(samples)
        log(f"样本数: {N}（近30天已结算预测 {len(rows)}，赛前有快照+有效特征）")
        seg_main = [s for s in samples if s["match_id"] >= 15000]
        log(f"  id>=15000 当前链路: {len(seg_main)}")
        base = sum(s["cold"] for s in samples) / N
        log(f"基线冷门率(市场热门失败): {base:.1%}")
        log("")

        # ── 单特征分离（AUC + 分桶）──
        from sklearn.metrics import roc_auc_score
        FEATS = [
            ("fav_prob", "市场热门隐含概率(收)"),
            ("fav_prob_open", "市场热门隐含概率(开)"),
            ("fav_drift", "热门隐含概率漂移(收-开)"),
            ("drift_h", "主胜隐含概率漂移"),
            ("drift_d", "平局隐含概率漂移"),
            ("drift_a", "客胜隐含概率漂移"),
            ("max_abs_drift", "最大绝对漂移"),
            ("disp", "bookmaker分散度(收)"),
            ("gl_shift", "大小球盘口移动"),
            ("hcp_shift", "亚盘盘口移动"),
            ("pre_hours", "赛前快照深度(小时)"),
            ("last_gap_hours", "距开赛间隔(小时)"),
            ("n_snap_times", "快照时刻数"),
            ("form_h", "主队近5场积分"),
            ("form_a", "客队近5场积分"),
            ("gf_h", "主队近5场进球"),
            ("ga_h", "主队近5场失球"),
            ("gf_a", "客队近5场进球"),
            ("ga_a", "客队近5场失球"),
            ("h2h_pts", "h2h主队积分"),
            ("h2h_n", "h2h场数"),
        ]
        log("===== 单特征对冷门标签的分离度 (AUC; 1.0=完美可分, 0.5=无信号) =====")
        auc_rows = []
        for key, desc in FEATS:
            vals = [(s[key], s["cold"]) for s in samples
                    if s[key] is not None and not (isinstance(s[key], float) and np.isnan(s[key]))]
            if len(vals) < 10 or len(set(v[1] for v in vals)) < 2:
                auc_rows.append((key, desc, np.nan, 0))
                continue
            x = np.array([v[0] for v in vals], dtype=float)
            y = np.array([v[1] for v in vals], dtype=float)
            try:
                auc = roc_auc_score(y, x)
            except ValueError:
                auc = np.nan
            auc_rows.append((key, desc, auc, len(vals)))
        for key, desc, auc, n in sorted(auc_rows, key=lambda r: -abs(r[2] - 0.5) if not np.isnan(r[2]) else -1):
            if np.isnan(auc):
                log(f"  {key:<14} {desc:<18} n={n:<4} 数据不足")
            else:
                log(f"  {key:<14} {desc:<18} n={n:<4} AUC={auc:.3f} {'<' if auc < 0.5 else '>'}0.5")

        # ── 主要特征分桶冷门率 ──
        def bucket_report(key, desc, nbins=4):
            vals = [(s[key], s["cold"]) for s in samples
                    if s[key] is not None and not (isinstance(s[key], float) and np.isnan(s[key]))]
            if len(vals) < nbins * 4:
                return
            vals.sort()
            log(f"\n  [{desc}] 分桶冷门率 (基线 {base:.1%}):")
            step = len(vals) // nbins
            for i in range(nbins):
                seg = vals[i*step: (i+1)*step if i < nbins-1 else len(vals)]
                r = sum(v[1] for v in seg) / len(seg)
                lo = seg[0][0]; hi = seg[-1][0]
                log(f"    {lo:.3f}~{hi:.3f}: n={len(seg)} 冷门率 {r:.1%}")
        bucket_report("fav_prob", "市场热门隐含概率(收)")
        bucket_report("fav_drift", "热门概率漂移")
        bucket_report("max_abs_drift", "最大绝对漂移")
        bucket_report("form_h", "主队近5场积分")
        bucket_report("form_a", "客队近5场积分")
        bucket_report("h2h_pts", "h2h主队积分")

        # ── 时间切分：逻辑回归 + LightGBM vs 基线（对全样本与当前链路分别验证）──
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        feat_cols = [k for k, _, auc, n in auc_rows if not np.isnan(auc)]

        def X_of(ds):
            X = []
            for s in ds:
                row = []
                for k in feat_cols:
                    v = s[k]
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        v = 0.0
                    row.append(float(v))
                X.append(row)
            return np.array(X)

        def time_split_eval(title, pool):
            if len(pool) < 30:
                log(f"\n===== [{title}] 样本不足 ({len(pool)})，跳过 =====")
                return
            log(f"\n===== 时间切分验证 [{title}] n={len(pool)} (训练=最早60%, 测试=后40%) =====")
            pool.sort(key=lambda s: s["kickoff"])
            n_train = int(len(pool) * 0.6)
            tr = pool[:n_train]
            te = pool[n_train:]
            log(f"训练 {len(tr)} 场 ({tr[0]['kickoff'].date()}~{tr[-1]['kickoff'].date()})")
            log(f"测试 {len(te)} 场 ({te[0]['kickoff'].date()}~{te[-1]['kickoff'].date()})")
            te_base = sum(s["cold"] for s in te) / len(te)
            log(f"测试期基线冷门率: {te_base:.1%}")

            Xtr, ytr = X_of(tr), np.array([s["cold"] for s in tr])
            Xte, yte = X_of(te), np.array([s["cold"] for s in te])
            if len(feat_cols) < 2 or len(set(ytr)) < 2:
                log("特征/标签不足以拟合")
                return
            sc = StandardScaler().fit(Xtr)
            clf = LogisticRegression(max_iter=1000, C=1.0)
            clf.fit(sc.transform(Xtr), ytr)
            proba = clf.predict_proba(sc.transform(Xte))[:, 1]
            order = np.argsort(-proba)
            te_sorted = [te[i] for i in order]
            te_cold = [s["cold"] for s in te_sorted]
            auc_te = roc_auc_score(yte, proba)
            log(f"逻辑回归测试 AUC: {auc_te:.3f} (特征数 {len(feat_cols)})")

            # LightGBM 对照（小数据防过拟合：强正则、小树）
            try:
                import lightgbm as lgb
                lgb_clf = lgb.LGBMClassifier(n_estimators=40, max_depth=2, learning_rate=0.05,
                                             num_leaves=7, min_child_samples=8,
                                             reg_alpha=1.0, reg_lambda=1.0,
                                             random_state=42, verbose=-1)
                lgb_clf.fit(Xtr, ytr)
                proba_lgb = lgb_clf.predict_proba(Xte)[:, 1]
                auc_lgb = roc_auc_score(yte, proba_lgb)
                order_l = np.argsort(-proba_lgb)
                te_cold_l = [s["cold"] for s in [te[i] for i in order_l]]
                log(f"LightGBM 测试 AUC: {auc_lgb:.3f}")
                for frac in (0.2, 0.3, 0.5):
                    k = max(1, int(len(te) * frac))
                    r = sum(te_cold_l[:k]) / k
                    log(f"  LGB top{frac:.0%}(n={k}): 冷门率 {r:.1%} vs 基线 {te_base:.1%}")
            except Exception as e:
                log(f"LightGBM 失败: {e}")

            for frac in (0.2, 0.3, 0.5):
                k = max(1, int(len(te) * frac))
                r = sum(te_cold[:k]) / k
                log(f"  LR top{frac:.0%}(n={k}): 冷门率 {r:.1%} vs 基线 {te_base:.1%}")
            # 每日 top_n 模拟（LR 得分）
            day_of = defaultdict(list)
            for s, p in zip(te, proba):
                kt = s["kickoff"]
                day = kt.date() if kt.hour >= 12 else (kt - timedelta(hours=12)).date()
                day_of[day].append((s, p))
            for top_n in (2, 3):
                picks, hits = 0, 0
                for day, ds in sorted(day_of.items()):
                    for s, p in sorted(ds, key=lambda x: -x[1])[:top_n]:
                        picks += 1
                        hits += s["cold"]
                log(f"  LR 每日 top{top_n}: {hits}/{picks} = {hits/picks:.1%} (冷门率)" if picks else f"  每日 top{top_n}: 无")
            # 基线对照：弱热门（fav_prob 升序）与 强热门（降序）
            for frac in (0.2, 0.3, 0.5):
                k = max(1, int(len(te) * frac))
                cand = sorted(te, key=lambda s: s["fav_prob"])[:k]
                r = sum(1 for s in cand if s["cold"]) / k
                log(f"  弱热门基线 top{frac:.0%}(n={k}): 冷门率 {r:.1%}")
            for frac in (0.2, 0.3):
                k = max(1, int(len(te) * frac))
                cand = sorted(te, key=lambda s: -s["fav_prob"])[:k]
                r = sum(1 for s in cand if s["cold"]) / k
                log(f"  强热门基线 top{frac:.0%}(n={k}): 冷门率 {r:.1%}（强热门越少爆冷→市场有信息）")
            # 每日 top2 明细（LR 得分）
            log("\n  测试期 LR 每日 top2 明细:")
            for day, ds in sorted(day_of.items()):
                top = sorted(ds, key=lambda x: -x[1])[:2]
                tag = "/".join("冷" if s["cold"] else "热" for s, _ in top)
                desc = "; ".join(f"{s['home']}v{s['away']}({DIRS[s['fav_last']]})" for s, _ in top)
                log(f"    {day}: {tag} | {desc}")

        time_split_eval("全样本", list(samples))
        seg_main_all = [s for s in samples if s["match_id"] >= 15000]
        time_split_eval("id>=15000 当前链路", seg_main_all)

        # ── 冷门候选明细（全样本，fav_drift+弱热门 综合，展示 top25）──
        log("\n===== 候选冷门场次示例 (按 fav_prob 升序=市场最弱热门, top25) =====")
        log(f"{'mid':>6} {'联赛':<5} {'对阵':<24} {'比分':<5} {'热门':<4} {'fav_p':>5} {'drift':>6} {'实际':<4}")
        for s in sorted(samples, key=lambda x: x["fav_prob"])[:25]:
            log(f"{s['match_id']:>6} {s['league'][:4]:<5} {(s['home']+'v'+s['away'])[:22]:<24} "
                f"{s['actual_hs']}:{s['actual_as']:<4} {DIRS[s['fav_last']]:<4} {s['fav_prob']:>5.2f} "
                f"{s['fav_drift']:>+6.2f} {DIRS[s['actual']]:<4}")

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        log(f"\n报告已写入 {REPORT_PATH}")

asyncio.run(main())
