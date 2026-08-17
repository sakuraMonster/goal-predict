"""盘口变化改进空间实证（水位漂移维度 + 整线位移 + 当前消费方向检验）

生产口径复刻（与 features_base._compute_ou_features 一致）：
  - best_gl = 开盘共识线（第一个 n_bm>=2 的时刻主盘线，1.5~3.5 通用过滤）
  - odds_drift_over_mean = 同线（abs(gl-best_gl)<0.001）逐 bm first_over - last_over 的均值
  - odds_drift_consensus = drift>0.01 的 bm 占比
  - goal_line_shift = 开盘共识线 - 近3时刻窗口众数共识线
  - Model C 消费: drift_signal=min(drift*3,1), shift_signal=min(shift,1.5)/1.5,
    combined_drop=0.7*drift_signal+0.3*shift_signal（同向*1.3）, drop_adj=-0.05*4*combined_drop（单向负向）

核心问题：
  Q1 水位漂移是否对 (实际-盘口线) 有方向预测力？corr 符号？
  Q2 当前"大球水位下降→压λ"的消费方向是否正确？
  Q3 combined_drop 触发时 Model C 命中率是提升还是受损？
"""
import asyncio
import json
import asyncpg
from collections import Counter, defaultdict

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
UNIVERSAL_OU_MIN, UNIVERSAL_OU_MAX = 1.5, 3.5
OU_GOAL_LINE_MIN_COUNT = 2
OU_CURRENT_GL_WINDOW = 3
DRIFT_SENS = 0.05  # default drop_sensitivity


def main_line_consensus(records):
    """records: list of (gl, ov, un, bm)"""
    bm_main = {}
    for gl, ov, un, bm in records:
        if ov is None or un is None:
            continue
        diff = abs(ov - un)
        if bm not in bm_main or diff < bm_main[bm][1]:
            bm_main[bm] = (gl, diff)
    gls = [v[0] for v in bm_main.values()]
    if not gls:
        return None, 0
    counter = Counter(gls)
    cand, cnt = counter.most_common(1)[0]
    if cnt / len(gls) >= 0.4:
        return cand, len(gls)
    sorted_gls = sorted(gls)
    return sorted_gls[len(sorted_gls) // 2], len(gls)


def compute_signals(all_ou):
    """返回 (opening_gl, current_gl, shift, drift_mean, drift_consensus)"""
    if not all_ou:
        return (None,) * 5
    valid = [(t, gl, ov, un, bm) for (t, gl, ov, un, bm) in all_ou
             if UNIVERSAL_OU_MIN <= gl <= UNIVERSAL_OU_MAX]
    times = sorted(set(t for (t, _, _, _, _) in all_ou))
    if not valid or not times:
        return (None,) * 5

    opening_gl = None
    for ot in times:
        recs = [(gl, ov, un, bm) for (t, gl, ov, un, bm) in valid if t == ot]
        cand, n_bm = main_line_consensus(recs)
        if cand is not None and n_bm >= OU_GOAL_LINE_MIN_COUNT:
            opening_gl = cand
            break

    current_gl = None
    window = []
    for wt in times[-OU_CURRENT_GL_WINDOW:]:
        recs = [(gl, ov, un, bm) for (t, gl, ov, un, bm) in valid if t == wt]
        cand, n_bm = main_line_consensus(recs)
        if cand is not None and n_bm >= OU_GOAL_LINE_MIN_COUNT:
            window.append(cand)
    if window:
        wc = Counter(window)
        mx = max(wc.values())
        current_gl = next(v for v in reversed(window) if wc[v] == mx)
    if current_gl is None:
        current_gl = opening_gl

    # 同线水位漂移（锚定 opening_gl 线）
    drift_mean = None
    drift_consensus = None
    if opening_gl is not None:
        same_line = [(t, ov, bm) for (t, gl, ov, un, bm) in valid
                     if abs(gl - opening_gl) < 0.001 and ov is not None]
        if same_line:
            bm_groups = defaultdict(list)
            for t, ov, bm in same_line:
                bm_groups[bm].append((t, ov))
            drift_vals = []
            for bm, recs in bm_groups.items():
                sr = sorted(recs, key=lambda x: x[0])
                if len(sr) >= 2:
                    drift_vals.append(sr[0][1] - sr[-1][1])
            if drift_vals:
                drift_mean = sum(drift_vals) / len(drift_vals)
                drift_consensus = sum(1 for d in drift_vals if d > 0.01) / len(drift_vals)

    shift = None
    if opening_gl is not None and current_gl is not None:
        shift = float(opening_gl) - float(current_gl)
    return opening_gl, current_gl, shift, drift_mean, drift_consensus


def combined_drop_of(drift_mean, shift):
    """复刻 Model C combined_drop 计算"""
    if drift_mean is None:
        drift_signal = 0.0
    else:
        drift_signal = min(max(drift_mean, 0.0) * 3.0, 1.0)
    shift_signal = min(max(shift or 0.0, 0.0), 1.5) / 1.5
    combined = 0.7 * drift_signal + 0.3 * shift_signal
    if drift_signal * shift_signal > 0:
        combined *= 1.3
    return max(0.0, min(1.0, combined))


async def main():
    conn = await asyncpg.connect(DSN)

    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, l.name_zh AS lg,
               th.name_zh AS hzh, ta.name_zh AS azh,
               m.kickoff_time,
               p.expected_goals_c, p.snap_top2_c, p.actual_total_goals
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        JOIN predictions p ON p.match_id = m.id
        WHERE m.kickoff_time >= '2026-06-01 00:00:00'
          AND p.actual_total_goals IS NOT NULL AND p.expected_goals_c IS NOT NULL
        ORDER BY m.kickoff_time
        """
    )
    ids = [r["id"] for r in rows]

    ou_map = defaultdict(list)
    if ids:
        for chunk in [ids[i:i + 500] for i in range(0, len(ids), 500)]:
            recs = await conn.fetch(
                """
                SELECT match_id, snapshot_time, bookmaker, goal_line, over_odds, under_odds
                FROM odds_snapshots
                WHERE match_id = ANY($1::int[]) AND goal_line IS NOT NULL
                ORDER BY snapshot_time
                """,
                chunk,
            )
            for r in recs:
                ou_map[r["match_id"]].append(
                    (r["snapshot_time"], r["goal_line"], r["over_odds"], r["under_odds"], r["bookmaker"])
                )
    await conn.close()

    out = []
    out.append("=" * 100)
    out.append("  盘口变化改进空间实证：水位漂移 + 整线位移 + Model C 消费方向")
    out.append(f"  样本: {len(rows)} 场已完场 (2026-06-01 起)")
    out.append("=" * 100)

    cases = []
    for r in rows:
        op, cur, shift, drift, dcons = compute_signals(ou_map.get(r["id"], []))
        if op is None or drift is None:
            continue
        cases.append((r, op, cur, shift, drift, dcons, len(ou_map.get(r["id"], []))))

    out.append(f"\n  可计算 drift 场次: {len(cases)} / {len(rows)}")
    if not cases:
        print("\n".join(out))
        return

    def hit(r):
        sc = json.loads(r["snap_top2_c"]) if isinstance(r["snap_top2_c"], str) else (r["snap_top2_c"] or [])
        return r["actual_total_goals"] in sc

    def corr(xs, ys):
        n = len(xs)
        if n < 2:
            return 0.0
        mx, my = sum(xs) / n, sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
        return num / den if den else 0.0

    # ── Q1: 相关性（基准线 = 开盘线，因为 drift/shift 均锚定开盘线） ──
    out.append("\n  —— Q1: 信号方向预测力（corr 越接近 0 越无预测力） ——")
    pairs_drift = []
    pairs_shift = []
    for r, op, cur, shift, drift, dcons, n in cases:
        if r["actual_total_goals"] is None:
            continue
        dev = r["actual_total_goals"] - op
        if drift is not None:
            pairs_drift.append((drift, dev, r))
        if shift is not None:
            pairs_shift.append((shift, dev, r))
    for name, pairs in [("水位漂移 drift vs (实际-开盘线)", pairs_drift),
                        ("整线位移 shift vs (实际-开盘线)", pairs_shift)]:
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        out.append(f"  corr({name}) = {corr(xs, ys):+.3f}  (n={len(pairs)})")

    # ── Q2: 方向桶 ──
    out.append("\n  —— Q2: 按水位漂移分桶（drift>0 = 大球水位下降; 正桶被当前逻辑当'看小'压λ） ——")
    def drift_bucket(d):
        if d >= 0.05: return "A大球水位下降≥0.05"
        if d >= 0.01: return "B下降0.01~0.05"
        if d > -0.01: return "C平稳±0.01"
        if d > -0.05: return "D上升0.01~0.05"
        return "E上升≥0.05"
    order = ["A大球水位下降≥0.05", "B下降0.01~0.05", "C平稳±0.01", "D上升0.01~0.05", "E上升≥0.05"]
    buckets = defaultdict(list)
    for r, op, cur, shift, drift, dcons, n in cases:
        buckets[drift_bucket(drift)].append((r, op, cur, shift, drift, dcons, n))
    out.append(f"  {'分桶':<22}{'n':>4} {'实际均值':>7} {'γC均值':>7} {'C命中':>8} {'大2.5率':>7} {'平均偏差':>7} {'corr(漂移,实际-线)':>18}")
    for g in order:
        items = buckets.get(g, [])
        if not items:
            continue
        acts = [i[0]["actual_total_goals"] for i in items]
        gcs = [i[0]["expected_goals_c"] for i in items]
        hits = sum(1 for i in items if hit(i[0]))
        over_rate = sum(1 for a in acts if a > 2.5) / len(acts) * 100
        devs = [gc - a for gc, a in zip(gcs, acts)]
        # 桶内 drift 与偏差的相关
        c = corr([i[4] for i in items], [i[0]["actual_total_goals"] - i[1] for i in items])
        out.append(f"  {g:<22}{len(items):>4} {sum(acts)/len(acts):>7.2f} {sum(gcs)/len(gcs):>7.2f} "
                   f"{hits:>3}/{len(items):<5}{hits/len(items)*100:>5.0f}% {over_rate:>6.0f}% {sum(devs)/len(devs):>+7.2f} {c:>+16.3f}")

    # ── Q3: combined_drop 触发效果 ──
    out.append("\n  —— Q3: Model C 的 combined_drop 触发时，命中是提升还是受损 ——")
    fired, notfired = [], []
    for r, op, cur, shift, drift, dcons, n in cases:
        cd = combined_drop_of(drift, shift)
        (fired if cd >= 0.15 else notfired).append((r, cd, drift, shift))
    for label, items in [("combined_drop≥0.15 (信号触发)", fired), ("combined_drop<0.15 (未触发)", notfired)]:
        if not items:
            continue
        acts = [i[0]["actual_total_goals"] for i in items]
        gcs = [i[0]["expected_goals_c"] for i in items]
        hits = sum(1 for i in items if hit(i[0]))
        out.append(f"  {label:<28} n={len(items):>3} 命中={hits:>3}/{len(items)} ({hits/len(items)*100:.0f}%) "
                   f"实际均值={sum(acts)/len(acts):.2f} γC均值={sum(gcs)/len(gcs):.2f}")

    # 触发时 drop_adj 的修正幅度分布
    out.append("\n  —— 触发场的 drop_adj 修正幅度（λ_market 层面） ——")
    adj = []
    for r, cd, drift, shift in fired:
        adj.append(-DRIFT_SENS * 4.0 * cd)
    if adj:
        out.append(f"  drop_adj 均值={sum(adj)/len(adj):.3f} 最小={min(adj):.3f} 最大={max(adj):.3f} "
                   f"(最大幅度 -0.20, 经 market_weight 0.6~0.9 后对最终 λ 仅 -12%~-18%)")

    # 触发 vs 未触发的偏差方向：drop_adj 只在触发时压低 λ，看是否因此更接近实际
    out.append("\n  —— 触发场压低λ后，偏差是否收窄（|γC-实际| 对比） ——")
    for label, items in [("触发", fired), ("未触发", notfired)]:
        if not items:
            continue
        devs = [abs(i[0]["expected_goals_c"] - i[0]["actual_total_goals"]) for i in items]
        out.append(f"  {label}: 平均绝对偏差={sum(devs)/len(devs):.2f}  n={len(items)}")

    # ── 附录: 08-14 周期逐场明细 ──
    out.append("\n  —— 附录: 08-14 竞彩周期逐场（drift>0=大球水位下降, 被当前逻辑当看小压λ） ——")
    out.append(f"  {'场次':<7}{'联赛':<6}{'对阵':<26}{'开盘':>4} {'当前':>4} {'shift':>6} {'drift':>6} {'一致率':>5} {'实际':>4} {'γC':>5} {'C命中':>5}")
    cycle = []
    for r, op, cur, shift, drift, dcons, n in cases:
        ko = r["kickoff_time"]
        if ko and ko >= __import__("datetime").datetime(2026, 8, 14, 12, 0) and ko < __import__("datetime").datetime(2026, 8, 15, 12, 0):
            cycle.append((r, op, cur, shift, drift, dcons))
    for r, op, cur, shift, drift, dcons in sorted(cycle, key=lambda x: x[0]["kickoff_time"]):
        sc = json.loads(r["snap_top2_c"]) if isinstance(r["snap_top2_c"], str) else (r["snap_top2_c"] or [])
        h = "中" if r["actual_total_goals"] in sc else "失"
        out.append(f"  {r['match_num']:<7}{r['lg']:<6}{r['hzh']+' vs '+r['azh']:<26}"
                   f"{op:>4.2f} {cur:>4.2f} {shift:>+5.2f} {drift:>+6.3f} {dcons*100:>4.0f}% "
                   f"{r['actual_total_goals']:>4} {r['expected_goals_c']:>5.2f} {h:>5}")

    text = "\n".join(out)
    with open(r"e:\zhangxuejun\new-thinking\ricking-03\backend\tools\_out_drift_empiric.txt", "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


asyncio.run(main())
