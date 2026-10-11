"""列出最近一个月命中「平赔被拉低」情报规则的场次，附信号与赛果（只读，不改任何数据）。

信号口径 = 「让方不胜」双选（让平 + 让负），即让球方未能让胜。
运行：docker exec -w /app -e PYTHONIOENCODING=utf-8 ricking-backend-1 python /tmp/list_intel.py
"""
import sys
import os
import asyncio
import importlib.util

sys.path.insert(0, "/app")

spec = importlib.util.spec_from_file_location("mf_new", "/tmp/mf_new.py")
new = importlib.util.module_from_spec(spec)
spec.loader.exec_module(new)

import asyncpg  # noqa: E402

SQL = """
SELECT m.id AS match_id,
       m.kickoff_time::date::text AS d,
       to_char(m.kickoff_time, 'MM-DD HH24:MI') AS ko,
       l.name_zh AS league,
       COALESCE(ht.name_zh, m.home_team_name) AS home,
       COALESCE(at.name_zh, m.away_team_name) AS away,
       m.home_score, m.away_score,
       s.had_home, s.had_draw, s.had_away, s.hhad_line,
       s.hhad_home, s.hhad_draw, s.hhad_away,
       s.snapshot_time::text AS st
FROM matches m
JOIN jczq_play_odds_snapshots s ON s.match_id = m.id
LEFT JOIN leagues l ON l.id = m.league_id
LEFT JOIN teams ht ON ht.id = m.home_team_id
LEFT JOIN teams at ON at.id = m.away_team_id
WHERE m.home_score IS NOT NULL AND m.away_score IS NOT NULL
  AND m.kickoff_time::date >= (CURRENT_DATE - INTERVAL '31 days')
  AND s.had_home IS NOT NULL AND s.had_draw IS NOT NULL AND s.had_away IS NOT NULL
ORDER BY m.kickoff_time, m.id, s.snapshot_time
"""


class Snap:
    def __init__(self, r):
        self.had_home = r["had_home"]
        self.had_draw = r["had_draw"]
        self.had_away = r["had_away"]
        self.hhad_line = r["hhad_line"]


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(SQL)
    await conn.close()

    last = {}
    for r in rows:
        last[r["match_id"]] = r

    hit = []
    for r in last.values():
        di = new._draw_intel(Snap(r))
        if not di:
            continue
        hs, as_ = r["home_score"], r["away_score"]
        line = float(r["hhad_line"])
        adj = hs + line
        # 赛果列：按「竞彩让球胜平负」口径（始终主队视角，与官方开奖一致）
        res = "让胜" if adj > as_ else ("让平" if adj == as_ else "让负")
        # 让方不胜（让方视角）：主队让球→让平/让负；客队让球→让胜/让平
        nb = res in (("让平", "让负") if line < 0 else ("让胜", "让平"))
        hit.append((r, di, res, nb))

    hit.sort(key=lambda x: (x[0]["d"], x[0]["ko"]), reverse=True)
    print(f"最近一个月(近31天)命中情报规则的场次：共 {len(hit)} 场")
    print("口径：让方不胜 = 让平 + 让负（双选）\n")
    print(f"{'日期':<11}{'时间':<6}{'联赛':<9}{'对阵':<24}{'让方':<4}{'让方赔':>7}{'平赔':>7}  "
          f"{'规则':<4}{'比分':>6}  {'让球结果':<8}{'让方不胜':<8}")
    print("-" * 116)
    n_nb = 0
    for r, di, res, nb in hit:
        n_nb += 1 if nb else 0
        vs = f"{(r['home'] or '?')[:11]} vs {(r['away'] or '?')[:11]}"
        fav_odds = r["had_home"] if di["fav"] == "home" else r["had_away"]
        fs = "主" if di["fav"] == "home" else "客"
        mark = "✓" if nb else "✗"
        score = f"{r['home_score']}-{r['away_score']}"
        print(f"{r['d']:<11}{r['ko'][-5:]:<6}{(r['league'] or '')[:8]:<9}{vs:<24}{fs:<4}"
              f"{fav_odds:>7.2f}{r['had_draw']:>7.2f}  {di['code']:<4}"
              f"{score:>6}  {res:<8}{mark:<8}")
    print("-" * 116)
    n_away = sum(1 for (r, di, res, nb) in hit if float(r["hhad_line"]) > 0)
    print(f"合计 {len(hit)} 场（其中客队让球 {n_away} 场）："
          f"让方不胜(让平+让负) {n_nb}/{len(hit)} = {n_nb/len(hit)*100:.1f}%"
          f"（全库基线约 74%，本信号组 lift 见规则表）")

    from collections import Counter
    print("\n按规则拆分（实际让球结果分布 / 让方不胜率）：")
    for code in ("I1", "I2", "I3", "I4", "I5", "I6"):
        seg = [x for x in hit if x[1]["code"] == code]
        if not seg:
            continue
        c = Counter(x[2] for x in seg)
        n = len(seg)
        nbs = c["让平"] + c["让负"]
        print(f"  {code} n={n:<3}: 让胜 {c['让胜']:>2}  让平 {c['让平']:>2}  让负 {c['让负']:>2}   "
              f"让方不胜 {nbs}/{n} = {nbs/n*100:>4.0f}%  (规则全样本 {seg[0][1]['sample_rate']*100:.1f}%)")

    # ---- Markdown 表格（排除 I1 强热门）----
    print("\n=== MARKDOWN（排除 I1）===")
    print("| 日期 | 联赛 | 对阵 | 让方 | 让方赔 | 平赔 | 让平赔 | 让负赔 | 规则 | 比分 | 让球结果 | 让方不胜 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    cnt = cnt_nb = 0
    fmt = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "-"
    for r, di, res, nb in hit:
        if di["code"] == "I1":
            continue
        cnt += 1
        cnt_nb += 1 if nb else 0
        fav_odds = r["had_home"] if di["fav"] == "home" else r["had_away"]
        fs = "主" if di["fav"] == "home" else "客"
        push_odds = r["hhad_draw"]                                      # 让平赔（走盘）
        loss_odds = r["hhad_away"] if di["fav"] == "home" else r["hhad_home"]  # 让负赔（让方未赢）
        print(f"| {r['d']} | {r['league'] or '国际赛事'} | {r['home']} vs {r['away']} | "
              f"{fs} | {fav_odds:.2f} | {r['had_draw']:.2f} | {fmt(push_odds)} | {fmt(loss_odds)} | {di['code']} | "
              f"{r['home_score']}-{r['away_score']} | {res} | {'✓' if nb else '✗'} |")
    print(f"\n排除I1后：{cnt} 场，让方不胜 {cnt_nb}/{cnt} = {cnt_nb/cnt*100:.1f}%")


asyncio.run(main())
