"""基础搏冷模型·因素补充探针（用户指正：原因素不对）

补测用户点名的方向中「现有数据可算」的部分：
1. 冷门构成：冷门是「热门平」还是「热门输」
2. 战意分层：联赛 vs 国内杯赛 vs 洲际/欧战（轮换/战意代理）
3. 赛程疲劳：上一场距今天数、近7天比赛场次（point-in-time）
4. 主客场细分：主队近5场主场积分 vs 客队近5场客场积分
5. 平局概率 draw_prob 分桶（平局倾向与冷门率）
6. 联赛维度冷门率分层
7. 顺手确认伤停/排名数据缺失现状（injury 条数、position 非空条数）

口径与 _cold_basic_model.py 一致：近30天当前链路(id>=15000)已结算 + 赛前快照
市场特征严格 snapshot_time < kickoff_time；基本面 date < kickoff（point-in-time）
输出：_out_cold_factor_more.txt
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

REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out_cold_factor_more.txt")
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

def last_n_form(recs, k=5, home_only=None):
    """近 k 场积分与得失球；home_only=None 不限, True 仅主场, False 仅客场"""
    pts, gf, ga, n = 0.0, 0, 0, 0
    for md, r, g1, g2, is_h in recs:
        if home_only is not None and is_h != home_only:
            continue
        if r in ("W", "D", "L"):
            pts += 3 if r == "W" else (1 if r == "D" else 0)
            n += 1
        if g1 is not None:
            gf += g1; ga += g2
        if n >= k:
            break
    return pts, gf, ga, n

def cup_flag(league_name):
    n = league_name or ""
    if any(k in n for k in ["欧冠", "欧罗巴", "欧会杯", "欧协联", "解放者杯", "亚冠", "世俱杯", "南美", "洲际"]):
        return "洲际/欧战"
    if "杯" in n:
        return "国内杯赛"
    return "联赛"

async def main():
    async with async_session() as db:
        # 数据缺失现状确认
        injury_n = (await db.execute(select(func.count()).select_from(Injury))).scalar() or 0
        pos_n = (await db.execute(select(func.count()).select_from(TeamSeasonStats)
                                  .where(TeamSeasonStats.league_position.isnot(None)))).scalar() or 0
        log(f"[数据现状] injury 表条数={injury_n}，team_season_stats 中 league_position 非空条数={pos_n}")
        log("")

        rows = (await db.execute(
            select(Prediction, Match).join(Match, Prediction.match_id == Match.id)
            .where(Prediction.result_spf != 0)
            .where(Match.kickoff_time >= W_START, Match.kickoff_time < W_END)
        )).all()
        match_ids = [p.match_id for p, _ in rows]
        kickoff = {p.match_id: m.kickoff_time for p, m in rows}
        teams = {p.match_id: (m.home_team_id, m.away_team_id) for p, m in rows}
        leagues = {p.match_id: (m.league.name_zh if m.league else "?") for p, m in rows}

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
            draw_prob = imp_last[1]

            htid, atid = teams.get(mid, (None, None))
            def best_recs(tid):
                if not tid:
                    return []
                best, best_n = [], -1
                for s in stats_by_team.get(tid, []):
                    recs = parse_recent(s.recent_matches, kt)
                    if len(recs) > best_n:
                        best_n, best = len(recs), recs
                return best
            rec_h = best_recs(htid)
            rec_a = best_recs(atid)

            # 赛程疲劳
            def fatigue(recs):
                if not recs:
                    return (None, None, None)  # rest_days, games_7d, games_3d
                last_d = recs[-1][0]
                rest = max(0.0, (kt.replace(tzinfo=None) - last_d.replace(tzinfo=None)).total_seconds() / 86400.0)
                g7 = sum(1 for md, *_ in recs if (kt - md).total_seconds() <= 7 * 86400)
                g3 = sum(1 for md, *_ in recs if (kt - md).total_seconds() <= 3 * 86400)
                return (rest, g7, g3)
            rest_h, g7_h, g3_h = fatigue(rec_h)
            rest_a, g7_a, g3_a = fatigue(rec_a)

            # 主客场细分积分（近5场内限主场/客场）
            hh_pts, _, _, hh_n = last_n_form(rec_h, home_only=True)
            aa_pts, _, _, aa_n = last_n_form(rec_a, home_only=False)
            home_gap = (hh_pts - aa_pts) if (hh_n and aa_n) else None

            # 综合近5场积分（对照用）
            fh = last_n_form(rec_h)[0]
            fa = last_n_form(rec_a)[0]

            samples.append({
                "match_id": mid, "kickoff": kt, "league": leagues.get(mid, "?"),
                "cup": cup_flag(leagues.get(mid, "")),
                "home": m.home_team.name_zh if m.home_team else (m.home_team_name or ""),
                "away": m.away_team.name_zh if m.away_team else (m.away_team_name or ""),
                "actual": actual, "fav_last": fav_last, "cold": cold,
                "fav_prob": imp_last[fav_last], "draw_prob": draw_prob,
                "rest_h": rest_h, "rest_a": rest_a, "g7_h": g7_h, "g7_a": g7_a, "g3_h": g3_h, "g3_a": g3_a,
                "hh_pts": hh_pts, "hh_n": hh_n, "aa_pts": aa_pts, "aa_n": aa_n,
                "home_gap": home_gap, "form_h": fh, "form_a": fa,
            })

        N = len(samples)
        base = sum(s["cold"] for s in samples) / N
        log(f"近30天当前链路有效样本: {N}，基线冷门率: {base:.1%}")
        log("")

        # 1) 冷门构成
        log("===== 1. 冷门构成（热门平 vs 热门输） =====")
        colds = [s for s in samples if s["cold"]]
        fav0 = sum(1 for s in colds if s["fav_last"] == 0)
        fav1 = sum(1 for s in colds if s["fav_last"] == 1)
        fav2 = sum(1 for s in colds if s["fav_last"] == 2)
        log(f"  冷门场次 {len(colds)}：主队热门爆冷 {fav0} | 平局热门爆冷 {fav1} | 客队热门爆冷 {fav2}")
        draw_actual = sum(1 for s in colds if s["actual"] == 1)
        log(f"  其中『热门平』(实际平局) {draw_actual} ({draw_actual/len(colds):.0%})，『热门直接输』 {len(colds)-draw_actual} ({(len(colds)-draw_actual)/len(colds):.0%})")
        log("")

        # 2) 战意分层
        log("===== 2. 战意分层（联赛 vs 国内杯赛 vs 洲际/欧战） =====")
        by_cup = defaultdict(list)
        for s in samples:
            by_cup[s["cup"]].append(s)
        for cup, seg in sorted(by_cup.items()):
            r = sum(1 for s in seg if s["cold"]) / len(seg)
            log(f"  {cup:<8} n={len(seg):<4} 冷门率 {r:.1%}")
        log("")
        # 国内杯赛内：欧战/杯赛 vs 联赛 Fisher
        log("  杯赛(含欧战) vs 联赛 Fisher 检验:")
        cup_all = [s for s in samples if s["cup"] != "联赛"]
        lg_all = [s for s in samples if s["cup"] == "联赛"]
        c1 = sum(1 for s in cup_all if s["cold"]); c0 = sum(1 for s in lg_all if s["cold"])
        table = [[c1, len(cup_all) - c1], [c0, len(lg_all) - c0]]
        _, p = fisher_exact(table)
        log(f"    杯赛/欧战 {c1}/{len(cup_all)}={c1/len(cup_all):.1%} vs 联赛 {c0}/{len(lg_all)}={c0/len(lg_all):.1%} (p={p:.3f})")
        log("")

        # 3) 赛程疲劳
        log("===== 3. 赛程疲劳 =====")
        def bucket(key, name, buckets, bins):
            log(f"  {name}:")
            for lo, hi in buckets:
                if lo is None:
                    seg = [s for s in samples if s[key] is None]
                elif hi is None:
                    seg = [s for s in samples if s[key] is not None and s[key] >= lo]
                else:
                    seg = [s for s in samples if s[key] is not None and lo <= s[key] < hi]
                if not seg:
                    continue
                r = sum(1 for s in seg if s["cold"]) / len(seg)
                note = bins.get((lo, hi), "")
                log(f"    {note or f'[{lo}~{hi})':<14} n={len(seg):<4} 冷门率 {r:.1%}")
        bucket("rest_h", "主队上一场距今天数(rest_h)", [(None, None), (0, 4), (4, 8), (8, None)], {(None, None): "无数据"})
        bucket("rest_a", "客队上一场距今天数(rest_a)", [(None, None), (0, 4), (4, 8), (8, None)], {(None, None): "无数据"})
        bucket("g7_h", "主队近7天场次", [(None, None), (0, 1), (1, 2), (2, None)], {(None, None): "无数据", (1, 2): "1场", (2, None): ">=2场"})
        bucket("g7_a", "客队近7天场次", [(None, None), (0, 1), (1, 2), (2, None)], {(None, None): "无数据", (1, 2): "1场", (2, None): ">=2场"})
        # Fisher：任一侧近7天>=2场
        busy = [s for s in samples if s["g7_h"] is not None and s["g7_a"] is not None and (s["g7_h"] >= 2 or s["g7_a"] >= 2)]
        rest = [s for s in samples if s["g7_h"] is not None and s["g7_a"] is not None and s["g7_h"] < 2 and s["g7_a"] < 2]
        if busy and rest:
            c1 = sum(1 for s in busy if s["cold"]); c0 = sum(1 for s in rest if s["cold"])
            _, p = fisher_exact([[c1, len(busy) - c1], [c0, len(rest) - c0]])
            log(f"    Fisher: 任一侧一周双赛 {c1}/{len(busy)}={c1/len(busy):.1%} vs 均单赛 {c0}/{len(rest)}={c0/len(rest):.1%} (p={p:.3f})")
        log("")

        # 4) 主客场细分
        log("===== 4. 主客场细分（主队近5主场积分 - 客队近5客场积分） =====")
        log(f"  home_gap 有值样本: {sum(1 for s in samples if s['home_gap'] is not None)}（主队近5主场 n 与客队近5客场 n 均有数据）")
        def gap_bucket(name, lo, hi, desc):
            seg = [s for s in samples if s["home_gap"] is not None and (lo is None or s["home_gap"] >= lo) and (hi is None or s["home_gap"] < hi)]
            if not seg:
                return
            r = sum(1 for s in seg if s["cold"]) / len(seg)
            log(f"    {desc:<14} n={len(seg):<4} 冷门率 {r:.1%}")
        gap_bucket("gap<-2", None, -2, "客强>2分")
        gap_bucket("gap[-2,2)", -2, 2, "接近")
        gap_bucket("gap>=2", 2, None, "主强>=2分")
        # 主队近5主场 vs 客队近5客场积分对比方向
        seg_home_edge = [s for s in samples if s["home_gap"] is not None and s["home_gap"] > 0]
        seg_away_edge = [s for s in samples if s["home_gap"] is not None and s["home_gap"] <= 0]
        for name, seg in (("主队近5主场积分占优", seg_home_edge), ("客队近5客场积分占优/持平", seg_away_edge)):
            if seg:
                r = sum(1 for s in seg if s["cold"]) / len(seg)
                log(f"    {name:<18} n={len(seg):<4} 冷门率 {r:.1%}")
        log("")

        # 5) 平局概率分桶
        log("===== 5. 平局隐含概率 draw_prob 分桶（平局倾向） =====")
        dbins = [(0.0, 0.24), (0.24, 0.28), (0.28, 0.32), (0.32, None)]
        for lo, hi in dbins:
            seg = [s for s in samples if (lo is None or s["draw_prob"] >= lo) and (hi is None or s["draw_prob"] < hi)]
            if len(seg) < 5:
                continue
            r = sum(1 for s in seg if s["cold"]) / len(seg)
            r_actual_draw = sum(1 for s in seg if s["actual"] == 1) / len(seg)
            fav_m = np.mean([s["fav_prob"] for s in seg])
            log(f"    draw_prob {lo:.2f}~{hi or 1.0:.2f}: n={len(seg):<4} 冷门率 {r:.1%} 实际平局率 {r_actual_draw:.1%} fav_prob均值 {fav_m:.3f}")
        log("")

        # 6) 双变量验证：新因素是否只是 fav_prob 弱热门的代理
        log("===== 6. 双变量验证（控制 fav_prob 后，新因素是否仍有区分度） =====")
        log("  全局 fav_prob 均值: {:.3f}".format(np.mean([s["fav_prob"] for s in samples])))
        weakk = [s for s in samples if s["home_gap"] is not None and s["home_gap"] < -2]
        log("  -- 客强>2分 样本 fav_prob 均值: {:.3f} (n={})".format(
            np.mean([s["fav_prob"] for s in weakk]), len(weakk)))
        log("  交叉表 (fav_prob<=0.50 vs >0.50) × (客强>2分 vs 其他):")
        for fp_lo, fp_hi, fp_desc in ((0.0, 0.50, "fav_prob<=0.50"), (0.50, 1.01, "fav_prob>0.50")):
            for g_lo, g_hi, g_desc in ((None, -2, "客强>2分"), (-2, None, "其他")):
                seg = [s for s in samples
                       if fp_lo <= s["fav_prob"] < fp_hi
                       and s["home_gap"] is not None
                       and (g_lo is None or s["home_gap"] >= g_lo) and (g_hi is None or s["home_gap"] < g_hi)]
                if len(seg) < 5:
                    continue
                r = sum(1 for s in seg if s["cold"]) / len(seg)
                log(f"    {fp_desc:<15} × {g_desc:<8} n={len(seg):<4} 冷门率 {r:.1%}")
        log("  交叉表 (fav_prob<=0.50 vs >0.50) × (draw_prob<0.24 vs >=0.24):")
        for fp_lo, fp_hi, fp_desc in ((0.0, 0.50, "fav_prob<=0.50"), (0.50, 1.01, "fav_prob>0.50")):
            for d_lo, d_hi, d_desc in ((0.0, 0.24, "draw<0.24"), (0.24, 1.01, "draw>=0.24")):
                seg = [s for s in samples
                       if fp_lo <= s["fav_prob"] < fp_hi and d_lo <= s["draw_prob"] < d_hi]
                if len(seg) < 5:
                    continue
                r = sum(1 for s in seg if s["cold"]) / len(seg)
                log(f"    {fp_desc:<15} × {d_desc:<10} n={len(seg):<4} 冷门率 {r:.1%}")
        log("")

        # 6) 联赛维度
        log("===== 6. 联赛维度冷门率（n>=8） =====")
        by_league = defaultdict(list)
        for s in samples:
            by_league[s["league"]].append(s)
        for lg, seg in sorted(by_league.items(), key=lambda kv: -sum(1 for s in kv[1] if s["cold"]) / len(kv[1])):
            if len(seg) < 8:
                continue
            r = sum(1 for s in seg if s["cold"]) / len(seg)
            log(f"    {lg:<10} n={len(seg):<4} 冷门率 {r:.1%}")
        log("")

        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        log(f"\n[已写报告] {REPORT_PATH}")

asyncio.run(main())
