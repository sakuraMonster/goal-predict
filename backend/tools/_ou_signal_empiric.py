"""盘口变化信号实证：goal_line_shift（开盘共识线→当前共识线）vs 实际进球
口径与 features_base._compute_ou_features 生产一致：
  - 通用过滤 1.5<=gl<=3.5（忽略联赛定制范围，先看全局）
  - 开盘共识线 = 第一个 n_bm>=2 的时刻主盘线
  - 当前共识线 = 近 OU_CURRENT_GL_WINDOW=3 个时刻逐批共识取众数（并列取最新批次）
  - shift = 开盘 - 当前（正=降盘看小，负=升盘看大）
样本：2026-07-01 起已完场且有 Model C 预测的比赛
"""
import asyncio
import asyncpg
from collections import Counter, defaultdict

DSN = "postgresql://postgres:postgres@localhost/football_prediction"
UNIVERSAL_OU_MIN, UNIVERSAL_OU_MAX = 1.5, 3.5
OU_GOAL_LINE_MIN_COUNT = 2
OU_CURRENT_GL_WINDOW = 3


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


def compute_shift(all_ou):
    """all_ou: list of (t, gl, ov, un, bm)（已按时间排序），返回 (opening_gl, current_gl, shift)"""
    if not all_ou:
        return None, None, None
    valid = [(t, gl, ov, un, bm) for (t, gl, ov, un, bm) in all_ou
             if UNIVERSAL_OU_MIN <= gl <= UNIVERSAL_OU_MAX]
    times = sorted(set(t for (t, _, _, _, _) in all_ou))
    if not valid or not times:
        return None, None, None

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

    if opening_gl is not None and current_gl is not None:
        return opening_gl, current_gl, float(opening_gl) - float(current_gl)
    return opening_gl, current_gl, None


async def main():
    conn = await asyncpg.connect(DSN)

    # 1) 目标比赛 + 预测
    rows = await conn.fetch(
        """
        SELECT m.id, m.match_num, l.name_zh AS lg,
               th.name_zh AS hzh, ta.name_zh AS azh,
               p.expected_goals_c, p.snap_top2_c, p.actual_total_goals
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        JOIN teams th ON th.id = m.home_team_id
        JOIN teams ta ON ta.id = m.away_team_id
        JOIN predictions p ON p.match_id = m.id
        WHERE m.kickoff_time >= '2026-07-01 00:00:00'
          AND p.actual_total_goals IS NOT NULL AND p.expected_goals_c IS NOT NULL
        ORDER BY m.kickoff_time
        """
    )
    ids = [r["id"] for r in rows]

    # 2) OU 快照
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

    print("=" * 108)
    print("  盘口变化信号实证（生产口径共识线；样本 2026-07-01 起已完场）")
    print("=" * 108)

    # 3) 逐场计算 shift
    cases = []
    for r in rows:
        all_ou = ou_map.get(r["id"], [])
        op, cur, shift = compute_shift(all_ou)
        if op is None or cur is None or shift is None:
            continue
        cases.append((r, op, cur, shift, len(all_ou)))

    print(f"  可计算 shift 场次: {len(cases)} / {len(rows)}")

    # 验证 15612 / 15608
    print("\n  —— 关键案例验证 ——")
    for r, op, cur, shift, n in cases:
        if r["id"] in (15612, 15608, 15611):
            print(f"  #{r['id']} {r['match_num']} [{r['lg']}] {r['hzh']} vs {r['azh']}: "
                  f"开盘共识={op} 当前共识={cur} shift={shift:+.2f} 快照数={n} "
                  f"实际={r['actual_total_goals']} γC={r['expected_goals_c']:.2f}")

    # 4) 分组统计
    def bucket(shift):
        if shift <= -0.25:
            return "升盘(看大)"
        if shift >= 0.25:
            return "降盘(看小)"
        return "平稳"

    groups = defaultdict(list)
    for r, op, cur, shift, n in cases:
        groups[bucket(shift)].append((r, op, cur, shift))

    print("\n  —— 分组统计 ——")
    print(f"  {'分组':<12}{'场次':>4} {'实际均值':>7} {'γC均值':>7} {'大2.5率':>7} {'C命中':>8} {'平均偏差':>7}")
    order = ["升盘(看大)", "平稳", "降盘(看小)"]
    import json
    for g in order:
        items = groups.get(g, [])
        if not items:
            print(f"  {g:<12}{0:>4}")
            continue
        acts = [r["actual_total_goals"] for r, _, _, _ in items]
        gcs = [r["expected_goals_c"] for r, _, _, _ in items]
        hit = 0
        for r, _, _, _ in items:
            sc = json.loads(r["snap_top2_c"]) if isinstance(r["snap_top2_c"], str) else (r["snap_top2_c"] or [])
            if r["actual_total_goals"] in sc:
                hit += 1
        devs = [gc - act for gc, act in zip(gcs, acts)]
        print(f"  {g:<12}{len(items):>4} {sum(acts)/len(acts):>7.2f} {sum(gcs)/len(gcs):>7.2f} "
              f"{sum(1 for a in acts if a>2.5)/len(acts)*100:>6.0f}% {hit:>3}/{len(items):<5}{hit/len(items)*100:>5.0f}% {sum(devs)/len(devs):>+7.2f}")

    # 5) shift 与 (实际-gl) 相关性
    print("\n  —— 相关性与一致性 ——")
    pairs = [(shift, r["actual_total_goals"] - cur) for r, op, cur, shift, n in cases]
    n = len(pairs)
    if n > 1:
        mean_s = sum(p[0] for p in pairs) / n
        mean_d = sum(p[1] for p in pairs) / n
        num = sum((p[0] - mean_s) * (p[1] - mean_d) for p in pairs)
        den = (sum((p[0] - mean_s) ** 2 for p in pairs) * sum((p[1] - mean_d) ** 2 for p in pairs)) ** 0.5
        corr = num / den if den else 0
        print(f"  corr(shift, 实际-当前线) = {corr:+.3f}  (n={n})")
    # 方向一致性：升盘 → 实际>当前线 的比例
    up = [(s, d) for s, d in pairs if s <= -0.25]
    dn = [(s, d) for s, d in pairs if s >= 0.25]
    if up:
        agree = sum(1 for _, d in up if d > 0.25)
        print(f"  升盘组: 实际高于当前线(>+0.25)占比 {agree}/{len(up)}={agree/len(up)*100:.0f}%")
    if dn:
        agree = sum(1 for _, d in dn if d < -0.25)
        print(f"  降盘组: 实际低于当前线(<-0.25)占比 {agree}/{len(dn)}={agree/len(dn)*100:.0f}%")


asyncio.run(main())
