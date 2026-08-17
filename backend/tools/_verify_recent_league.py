"""验证方案：最近竞彩比赛（2026-07-01 以来，id 155xx）league_id 反推球队真实联赛
1. 2026-07-01 以来 matches 按 league_id 分布（确认竞彩比赛干净）
2. 竞彩比赛定义：jc_match_id 非空 vs id>=15000 的数量差异
3. 每队最近一场干净比赛的 league_id → 反推真实联赛
4. 反推 vs 当前 Team.league_id 对比统计 + 葡超重点球队
5. 识别杯赛/跨联赛比赛（同一球队最近比赛 league_id 与第二近不一致的情况）
"""
import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Team, Match, League

CUTOFF = datetime(2026, 7, 1, 0, 0)
OUT = os.path.join(os.path.dirname(__file__), "recent_league_diag.txt")


async def main():
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        league_name = {}
        r = await db.execute(select(League.id, League.name_zh))
        for lg_id, nz in r.all():
            league_name[lg_id] = nz

        # 1. 2026-07-01 以来 matches 分布
        r = await db.execute(
            select(Match.league_id, func.count(Match.id))
            .where(Match.kickoff_time >= CUTOFF)
            .group_by(Match.league_id).order_by(func.count(Match.id).desc())
        )
        p("== 1. 2026-07-01 以来 matches 按 league_id 分布 ==")
        clean_total = 0
        for lg_id, cnt in r.all():
            clean_total += cnt
            p(f"  league_id={lg_id} ({league_name.get(lg_id, '?')}): {cnt}")
        p(f"  合计: {clean_total}")

        # 2. 竞彩比赛标识：jc_match_id 非空数量
        r = await db.execute(
            select(func.count(Match.id)).where(Match.kickoff_time >= CUTOFF, Match.jc_match_id.isnot(None))
        )
        p(f"\n== 2. 2026-07-01 以来 jc_match_id 非空: {r.scalar()} ==")
        r = await db.execute(
            select(func.min(Match.id), func.max(Match.id)).where(Match.kickoff_time >= CUTOFF)
        )
        mn, mx = r.one()
        p(f"  id 范围: {mn} ~ {mx}")

        # 3. 每队最近一场干净比赛 league_id
        # 取 2026-07-01 以来每个球队参与的最近比赛（按 kickoff_time desc, id desc 排序取前1）
        r = await db.execute(
            select(Match.id, Match.home_team_id, Match.away_team_id, Match.league_id, Match.kickoff_time)
            .where(Match.kickoff_time >= CUTOFF, Match.home_team_id.isnot(None), Match.away_team_id.isnot(None))
            .order_by(Match.kickoff_time.desc(), Match.id.desc())
        )
        rows = r.all()
        team_recent = {}   # team_id -> (league_id, match_id, kickoff)
        team_second = {}   # team_id -> second league_id (用于检测杯赛)
        for m in rows:
            for tid in (m.home_team_id, m.away_team_id):
                if tid not in team_recent:
                    team_recent[tid] = (m.league_id, m.id, m.kickoff_time)
                elif tid not in team_second:
                    team_second[tid] = (m.league_id, m.id, m.kickoff_time)
        p(f"\n== 3. 有干净比赛的球队数: {len(team_recent)} ==")

        # 反推 vs 当前
        teams_r = await db.execute(select(Team.id, Team.sportmonks_id, Team.name_zh, Team.league_id))
        teams = teams_r.all()
        cur_map = {t.id: (t.sportmonks_id, t.name_zh, t.league_id) for t in teams}

        same = diff = no_rec = 0
        diff_list = []
        for tid, (sm, name, cur) in cur_map.items():
            if tid not in team_recent:
                no_rec += 1
                continue
            inf_lg, mid, kt = team_recent[tid]
            if cur == inf_lg:
                same += 1
            else:
                diff += 1
                diff_list.append((tid, sm, name, cur, inf_lg, mid, kt))
        p(f"  与当前 Team.league_id 一致: {same}")
        p(f"  不一致: {diff}（样本前30如下）")
        p(f"  无干净比赛记录: {no_rec}")

        diff_list.sort(key=lambda x: (x[3] != 6, x[4] != 6, x[1] or 0))  # 当前=6 优先展示
        for tid, sm, name, cur, inf_lg, mid, kt in diff_list[:30]:
            p(f"  id={tid} sm={sm} {name}: 当前={cur}({league_name.get(cur,'?')}) 反推={inf_lg}({league_name.get(inf_lg,'?')}) 最近比赛id={mid} t={kt}")

        # 4. 葡超重点球队（id 已知）
        p("\n== 4. 葡超重点球队最近比赛 ==")
        focus = [1299, 1678, 1646, 208, 1662, 1680, 1651, 785, 388, 389, 401, 408, 422, 486]
        for tid in focus:
            sm, name, cur = cur_map.get(tid, (None, "?", None))
            inf = team_recent.get(tid)
            sec = team_second.get(tid)
            if inf:
                inf_str = f"反推={inf[0]}({league_name.get(inf[0],'?')}) 比赛id={inf[1]} t={inf[2]}"
                if sec and sec[0] != inf[0]:
                    inf_str += f" || 第二近={sec[0]}({league_name.get(sec[0],'?')}) id={sec[1]} t={sec[2]}"
            else:
                inf_str = "无干净比赛"
            p(f"  id={tid} sm={sm} {name}: 当前={cur}({league_name.get(cur,'?')}) {inf_str}")

        # 5. 杯赛检测：最近 vs 第二近 league_id 不一致的球队数
        p("\n== 5. 最近与第二近比赛 league_id 不一致（疑似杯赛/跨联赛）==")
        multi = [(tid, team_recent[tid], team_second[tid], cur_map.get(tid, (None, "?", None))[2])
                 for tid in team_second if team_second[tid][0] != team_recent[tid][0]]
        p(f"  数量: {len(multi)}")
        for tid, (l1, i1, t1), (l2, i2, t2), cur in multi[:20]:
            p(f"  id={tid} {cur_map.get(tid, (None,'?',None))[1]}: 最近={l1}({league_name.get(l1,'?')}) id={i1} t={t1} | 第二近={l2}({league_name.get(l2,'?')}) id={i2} t={t2}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出已写入: {OUT}")


asyncio.run(main())
