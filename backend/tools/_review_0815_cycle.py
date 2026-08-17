"""08-14 比赛周期深度复盘
周期定义：2026-08-14 12:00 ~ 2026-08-15 12:00（北京时间 naive，竞彩口径）

输出：
  1. 每场比赛完整预测信息（Model C/D SNAP、SPF、HCP、大2.5、冷门、信心、评分）
  2. 赔率快照：开盘线 vs 最新线（goal_line 变化 / 让球线 / 欧赔）
  3. 已完场比赛命中判定
  4. 联赛/维度汇总
"""
import asyncio
import asyncpg
import json
from collections import defaultdict

DSN = "postgresql://postgres:postgres@localhost/football_prediction"

SQL_MATCHES = """
SELECT m.id, m.jc_match_id, m.match_num, m.kickoff_time, m.status,
       m.home_score, m.away_score, m.half_home_score, m.half_away_score,
       m.handicap_line, m.is_swapped, m.sportmonks_fixture_id AS fx,
       l.name_zh AS lg,
       th.name_zh AS hzh, th.name_en AS hen, th.sportmonks_id AS hsm,
       ta.name_zh AS azh, ta.name_en AS aen, ta.sportmonks_id AS asm,
       p.id AS pid, p.model_version,
       p.expected_goals, p.expected_goals_c, p.expected_goals_d,
       p.snap_top2, p.snap_top2_c, p.snap_top2_d,
       p.home_prob, p.draw_prob, p.away_prob,
       p.handicap_home_prob, p.handicap_draw_prob, p.handicap_away_prob,
       p.over_2_5_prob, p.goal_distribution, p.score_top5_json,
       p.actual_home_score, p.actual_away_score, p.actual_total_goals, p.actual_score,
       p.result_spf, p.result_hcp, p.result_goals, p.result_score,
       p.confidence_level, p.is_cold_match, p.cold_correction,
       p.summary_text, p.key_factors, p.risk_warning
FROM matches m
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN teams th ON th.id = m.home_team_id
LEFT JOIN teams ta ON ta.id = m.away_team_id
LEFT JOIN predictions p ON p.match_id = m.id
WHERE m.kickoff_time >= '2026-08-14 12:00:00' AND m.kickoff_time < '2026-08-15 12:00:00'
ORDER BY m.kickoff_time
"""

SQL_ODDS_SUMMARY = """
SELECT o.match_id,
       count(*) AS cnt, count(DISTINCT o.bookmaker) AS bm_cnt,
       min(o.snapshot_time) AS earliest, max(o.snapshot_time) AS latest
FROM odds_snapshots o
WHERE o.match_id = ANY($1::int[])
GROUP BY o.match_id
"""

SQL_OPENING = """
SELECT DISTINCT ON (o.match_id) o.match_id, o.snapshot_time, o.bookmaker,
       o.home_win, o.draw, o.away_win,
       o.handicap_home, o.handicap_line, o.handicap_away,
       o.over_odds, o.goal_line, o.under_odds
FROM odds_snapshots o
WHERE o.match_id = ANY($1::int[]) AND o.is_opening = TRUE
ORDER BY o.match_id, o.snapshot_time
"""

SQL_LATEST = """
SELECT DISTINCT ON (o.match_id) o.match_id, o.snapshot_time, o.bookmaker,
       o.home_win, o.draw, o.away_win,
       o.handicap_home, o.handicap_line, o.handicap_away,
       o.over_odds, o.goal_line, o.under_odds
FROM odds_snapshots o
WHERE o.match_id = ANY($1::int[])
ORDER BY o.match_id, o.snapshot_time DESC
"""


def jload(x):
    if x is None:
        return None
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return None
    return x


def fmt_odds(v):
    if v is None:
        return "-"
    return f"{v:.2f}"


def fmt_pct(v):
    if v is None:
        return "-"
    return f"{v*100:.0f}%"


def main_report(rows, odds_sum, opening_map, latest_map):
    print("=" * 118)
    print("  08-14 比赛周期深度复盘（08-14 12:00 ~ 08-15 12:00 北京时间）")
    print("=" * 118)
    print(f"  共 {len(rows)} 场\n")

    by_league = defaultdict(list)
    for r in rows:
        by_league[r["lg"] or "未知"].append(r)

    # ── 逐场明细 ──
    for r in rows:
        snap_c = jload(r["snap_top2_c"]) or []
        snap_d = jload(r["snap_top2_d"]) or []
        snap_main = jload(r["snap_top2"]) or []
        top5 = jload(r["score_top5_json"]) or []
        cold = jload(r["cold_correction"])

        status_mark = {"scheduled": "未开赛", "finished": "已完场", "cancelled": "已忽略"}.get(r["status"], r["status"])
        hname = r["hzh"] or r["hen"]
        aname = r["azh"] or r["aen"]

        print("-" * 118)
        print(f"  #{r['id']} [{r['lg']}] {r['match_num']} ko={r['kickoff_time']} status={status_mark} fx={r['fx']}")
        print(f"    主: {hname} (sm={r['hsm']})    客: {aname} (sm={r['asm']})")
        if r["is_swapped"]:
            print(f"    ⚠ is_swapped=True（SM 主客与竞彩相反）")

        # 预测部分
        if r["pid"]:
            print(f"    预测 v{r['model_version']}  信心={r['confidence_level']}  冷门={'是' if r['is_cold_match'] else '否'}")
            print(f"      γ(全局)={r['expected_goals']:.2f}  γC={r['expected_goals_c']:.2f}  γD={r['expected_goals_d'] if r['expected_goals_d'] is not None else float('nan'):.2f}")
            print(f"      SNAP(全局)={snap_main}  SNAP_C={snap_c}  SNAP_D={snap_d}")
            print(f"      SPF: 主{fmt_pct(r['home_prob'])} 平{fmt_pct(r['draw_prob'])} 客{fmt_pct(r['away_prob'])}"
                  f"   HCP: 主{fmt_pct(r['handicap_home_prob'])} 平{fmt_pct(r['handicap_draw_prob'])} 客{fmt_pct(r['handicap_away_prob'])}"
                  f"   大2.5={fmt_pct(r['over_2_5_prob'])}")
            if top5:
                top5s = ", ".join(str(t) for t in top5[:5])
                print(f"      比分Top5: {top5s}")
            if cold:
                print(f"      冷门修正: {json.dumps(cold, ensure_ascii=False)[:160]}")
            if r["key_factors"]:
                kf = r["key_factors"].replace("\n", " ")
                print(f"      关键因素: {kf[:220]}")
            if r["risk_warning"]:
                rw = r["risk_warning"].replace("\n", " ")
                print(f"      风险提示: {rw[:220]}")
            if r["summary_text"]:
                st = r["summary_text"].replace("\n", " ")
                print(f"      摘要: {st[:220]}")
        else:
            print(f"    ⚠ 无预测记录")

        # 赔率部分
        o = odds_sum.get(r["id"])
        if o:
            op = opening_map.get(r["id"])
            lt = latest_map.get(r["id"])
            print(f"    赔率: {o['cnt']}条/{o['bm_cnt']}家  {o['earliest']:%m-%d %H:%M} ~ {o['latest']:%m-%d %H:%M}")
            if op and lt:
                def line_diff(a, b, label):
                    if a is None or b is None:
                        return f"{label}: -"
                    d = b - a
                    arrow = "↑" if d > 0.01 else ("↓" if d < -0.01 else "→")
                    return f"{label}: {a:.2f}→{b:.2f}({d:+.2f}{arrow})"
                print(f"      开盘[{op['bookmaker']}] 欧赔 {fmt_odds(op['home_win'])}/{fmt_odds(op['draw'])}/{fmt_odds(op['away_win'])}"
                      f"  让球 {fmt_odds(op['handicap_line'])}  大小 {fmt_odds(op['goal_line'])}(O{fmt_odds(op['over_odds'])}/U{fmt_odds(op['under_odds'])})")
                print(f"      最新[{lt['bookmaker']}] 欧赔 {fmt_odds(lt['home_win'])}/{fmt_odds(lt['draw'])}/{fmt_odds(lt['away_win'])}"
                      f"  让球 {fmt_odds(lt['handicap_line'])}  大小 {fmt_odds(lt['goal_line'])}(O{fmt_odds(lt['over_odds'])}/U{fmt_odds(lt['under_odds'])})")
                print(f"      盘口移动: {line_diff(op['handicap_line'], lt['handicap_line'], '让球')}   {line_diff(op['goal_line'], lt['goal_line'], '大小')}")
            elif lt:
                print(f"      最新[{lt['bookmaker']}] 欧赔 {fmt_odds(lt['home_win'])}/{fmt_odds(lt['draw'])}/{fmt_odds(lt['away_win'])}"
                      f"  让球 {lt['handicap_line']}  大小 {lt['goal_line']}")
        else:
            print(f"    ⚠ 无赔率快照")

        # 实际结果
        if r["status"] == "finished" or r["actual_total_goals"] is not None:
            act = r["actual_total_goals"]
            print(f"    实际: {r['actual_home_score']}-{r['actual_away_score']}  总进球={act}  比分={r['actual_score']}")
            if r["pid"] and act is not None:
                cap = min(act, 4)
                hit_c = cap in snap_c
                hit_d = cap in snap_d
                print(f"    → 进球判定: C={hit_c}({'✓' if hit_c else '✗'}) D={hit_d}({'✓' if hit_d else '✗'})  "
                      f"result_goals={r['result_goals']} result_spf={r['result_spf']} result_hcp={r['result_hcp']}")
        print()

    # ── 汇总 ──
    print("=" * 118)
    print("  汇总")
    print("=" * 118)
    n_pred = sum(1 for r in rows if r["pid"])
    n_sched = sum(1 for r in rows if r["status"] == "scheduled")
    n_fin = sum(1 for r in rows if r["status"] == "finished")
    n_cancelled = sum(1 for r in rows if r["status"] == "cancelled")
    print(f"  总场次={len(rows)}  已预测={n_pred}  未开赛={n_sched}  已完场={n_fin}  已忽略={n_cancelled}")

    # 命中统计（仅统计有实际结果的场次；不依赖 match.status）
    settled = [r for r in rows if r["actual_total_goals"] is not None and r["pid"]]
    hit_c = hit_d = hit_spf = hit_hcp = 0
    spf_settled = hcp_settled = 0
    devs = []
    for r in settled:
        sc = jload(r["snap_top2_c"]) or []
        sd = jload(r["snap_top2_d"]) or []
        act = r["actual_total_goals"]
        cap = min(act, 4)
        if sc and cap in sc:
            hit_c += 1
        if sd and cap in sd:
            hit_d += 1
        if r["result_spf"] in (1, -1):
            spf_settled += 1
            if r["result_spf"] == 1:
                hit_spf += 1
        if r["result_hcp"] in (1, -1):
            hcp_settled += 1
            if r["result_hcp"] == 1:
                hit_hcp += 1
        if r["expected_goals_c"] is not None:
            devs.append((r, r["expected_goals_c"] - act))

    if settled:
        print(f"\n  已结算={len(settled)}  进球命中: Model C {hit_c}/{len(settled)}={hit_c/len(settled)*100:.1f}%  "
              f"Model D {hit_d}/{len(settled)}={hit_d/len(settled)*100:.1f}%")
    if spf_settled:
        print(f"  SPF 结算={spf_settled}  命中={hit_spf}={hit_spf/spf_settled*100:.1f}%")
    if hcp_settled:
        print(f"  HCP 结算={hcp_settled}  命中={hit_hcp}={hit_hcp/hcp_settled*100:.1f}%")

    # 偏差分析（γC vs 实际）
    if devs:
        avg_dev = sum(d[1] for d in devs) / len(devs)
        over = [d for d in devs if d[1] > 0.3]
        under = [d for d in devs if d[1] < -0.3]
        print(f"\n  偏差分析: 平均偏差 γC-实际 = {avg_dev:+.2f}  高估场次={len(over)} 低估场次={len(under)}")
        print("\n  高估场次（γC 明显 > 实际）:")
        for r, dev in sorted(over, key=lambda x: -x[1])[:6]:
            print(f"    [{r['lg']}] {(r['hzh'] or r['hen'])} vs {(r['azh'] or r['aen'])} 实际={r['actual_total_goals']}球 γC={r['expected_goals_c']:.2f} 偏差={dev:+.2f}")
        print("\n  低估场次（γC 明显 < 实际）:")
        for r, dev in sorted(under, key=lambda x: x[1])[:6]:
            print(f"    [{r['lg']}] {(r['hzh'] or r['hen'])} vs {(r['azh'] or r['aen'])} 实际={r['actual_total_goals']}球 γC={r['expected_goals_c']:.2f} 偏差={dev:+.2f}")

    print(f"\n  按联赛分布:")
    for lg, items in sorted(by_league.items(), key=lambda x: -len(x[1])):
        s = [r for r in items if r["actual_total_goals"] is not None and r["pid"]]
        hc = sum(1 for r in s if (jload(r["snap_top2_c"]) or []) and min(r["actual_total_goals"], 4) in jload(r["snap_top2_c"]))
        rate = f"{hc}/{len(s)}={hc/len(s)*100:.0f}%" if s else "-"
        print(f"    {lg:<10} {len(items)}场  已结算{len(s)}  C命中{rate}")

    # 冷门统计
    colds = [(r, jload(r["snap_top2_c"])) for r in rows if r["is_cold_match"]]
    if colds:
        print(f"\n  冷门标记比赛 {len(colds)} 场:")
        for r, sc in colds:
            print(f"    #{r['id']} [{r['lg']}] {(r['hzh'] or r['hen'])} vs {(r['azh'] or r['aen'])} SNAP_C={sc} 信心={r['confidence_level']}")


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch(SQL_MATCHES)
    match_ids = [r["id"] for r in rows]

    odds_sum = {}
    opening_map = {}
    latest_map = {}
    if match_ids:
        for r in await conn.fetch(SQL_ODDS_SUMMARY, match_ids):
            odds_sum[r["match_id"]] = r
        for r in await conn.fetch(SQL_OPENING, match_ids):
            opening_map[r["match_id"]] = r
        for r in await conn.fetch(SQL_LATEST, match_ids):
            latest_map[r["match_id"]] = r

    await conn.close()
    return rows, odds_sum, opening_map, latest_map


if __name__ == "__main__":
    import sys
    rows, odds_sum, opening_map, latest_map = asyncio.run(main())
    out_path = "tools/_out_0814_review.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        old = sys.stdout
        sys.stdout = f
        try:
            main_report(rows, odds_sum, opening_map, latest_map)
        finally:
            sys.stdout = old
    print(f"已写入 {out_path}")
