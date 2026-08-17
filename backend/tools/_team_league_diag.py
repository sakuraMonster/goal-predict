"""诊断：matches 表反推 Team.league_id（多数票），评估修复可行性
统计：
1. Team.league_id 与反推 league_id 不一致的数量
2. 反推票数/置信度分布
3. 无法反推（无 match 记录）的球队数
"""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Team, Match

OUT = os.path.join(os.path.dirname(__file__), "team_league_fix_diag.txt")


async def main():
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        teams_r = await db.execute(select(Team.id, Team.sportmonks_id, Team.name_zh, Team.league_id))
        teams = teams_r.all()
        p(f"Team 总数: {len(teams)}")

        # 每队参与的比赛 league_id 票数
        # 一次性查所有 match 的 league 归属（按队聚合太重，用 SQL 聚合）
        home_r = await db.execute(
            select(Match.home_team_id, Match.league_id, func.count(Match.id))
            .where(Match.home_team_id.isnot(None))
            .group_by(Match.home_team_id, Match.league_id)
        )
        away_r = await db.execute(
            select(Match.away_team_id, Match.league_id, func.count(Match.id))
            .where(Match.away_team_id.isnot(None))
            .group_by(Match.away_team_id, Match.league_id)
        )
        votes = Counter()  # (team_id, league_id) -> count
        for tid, lg, cnt in home_r.all():
            votes[(tid, lg)] += cnt
        for tid, lg, cnt in away_r.all():
            votes[(tid, lg)] += cnt

        # 每队反推
        inferred = {}  # team_id -> (league_id, votes, total)
        for tid, sm, name, cur in teams:
            league_votes = Counter()
            for (t, lg), cnt in votes.items():
                if t == tid:
                    league_votes[lg] += cnt
            if not league_votes:
                inferred[tid] = (None, 0, 0, None)
            else:
                top_lg, top_cnt = league_votes.most_common(1)[0]
                total = sum(league_votes.values())
                inferred[tid] = (top_lg, top_cnt, total, top_cnt / total if total else 0)

        # 统计
        changed = []
        uncertain = []
        no_match = []
        ok = 0
        for tid, sm, name, cur in teams:
            top_lg, top_cnt, total, ratio = inferred[tid]
            if top_lg is None:
                no_match.append((tid, sm, name, cur))
                continue
            if cur == top_lg:
                ok += 1
            else:
                changed.append((tid, sm, name, cur, top_lg, top_cnt, total, ratio))

        p(f"已一致: {ok}")
        p(f"不一致: {len(changed)}（其中置信度: ratio>=0.8 的 {sum(1 for c in changed if c[7] >= 0.8)} 个）")
        p(f"无 match 记录: {len(no_match)}")

        p("\n== 不一致且置信度>=0.8 的样本（前30）==")
        high = [c for c in changed if c[7] >= 0.8]
        high.sort(key=lambda c: -c[6])
        for tid, sm, name, cur, top_lg, top_cnt, total, ratio in high[:30]:
            p(f"  id={tid} sm={sm} {name}: 当前={cur} 反推={top_lg} 票={top_cnt}/{total} ratio={ratio:.0%}")

        p("\n== 不一致且置信度<0.8（前20，留人检）==")
        low = [c for c in changed if c[7] < 0.8]
        for tid, sm, name, cur, top_lg, top_cnt, total, ratio in low[:20]:
            p(f"  id={tid} sm={sm} {name}: 当前={cur} 反推={top_lg} 票={top_cnt}/{total} ratio={ratio:.0%}")

        p("\n== 无 match 记录（前20）==")
        for tid, sm, name, cur in no_match[:20]:
            p(f"  id={tid} sm={sm} {name}: 当前={cur}")

        # 重点球队验证
        p("\n== 葡超重点球队 ==")
        for tid in [1299, 1678, 1646, 208, 1662, 1680, 1651, 785, 388, 389, 401, 408, 422]:
            top_lg, top_cnt, total, ratio = inferred[tid]
            cur = next((t.league_id for t in teams if t.id == tid), None)
            name = next((t.name_zh for t in teams if t.id == tid), "?")
            p(f"  id={tid} {name}: 当前={cur} 反推={top_lg} 票={top_cnt}/{total} ratio={ratio:.0%}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出已写入: {OUT}")


asyncio.run(main())
