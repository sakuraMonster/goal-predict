"""t1v2: 修正 Team.league_id（含互证逻辑）

规则：
1. 收集每队 2026-07-01 以来比赛 league_id 票（排除杯赛 id、None）
   杯赛识别修正：仅匹配明确杯赛/洲际联赛（欧冠/欧罗巴/欧协联/各国杯/Champions League/Europa/Cup），
   不再误匹配 Championship（英冠，国内联赛）。
2. 多数票（平局取 kickoff 最近）
3. 更新条件：
   - 反推 != 当前
   - 票数 >= 2，或 票数 == 1 且 当前 in {None, 6}
   - 或 票数 == 1 且 对手互证（该场比赛对手的多数票联赛 == 反推联赛）
4. 打印全部变更 + 未变更留人检
"""
import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Team, Match, League

CUTOFF = datetime(2026, 7, 1, 0, 0)
OUT = os.path.join(os.path.dirname(__file__), "team_league_fix_result.txt")


def is_cup(zh, en):
    n = f"{zh or ''}{en or ''}"
    if "杯" in (zh or ""):
        return True
    return any(k in n for k in ["Champions League", "Europa", "Conference League", "Cup", "Super Cup"])


async def main():
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        r = await db.execute(select(League.id, League.name_zh, League.name_en, League.country))
        leagues = r.all()
        league_name = {lg.id: lg.name_zh for lg in leagues}
        cup_ids = {lg.id for lg in leagues if is_cup(lg.name_zh, lg.name_en)}
        p(f"== 杯赛/洲际联赛 id（排除）: {sorted(cup_ids)} ==")
        for cid in sorted(cup_ids):
            p(f"    {cid}: {league_name.get(cid, '?')}")

        # 每队比赛明细: team_id -> [(league_id, opp_team_id, kickoff)]
        r = await db.execute(
            select(Match.home_team_id, Match.away_team_id, Match.league_id, Match.kickoff_time)
            .where(Match.kickoff_time >= CUTOFF,
                   Match.home_team_id.isnot(None), Match.away_team_id.isnot(None),
                   Match.league_id.isnot(None))
            .order_by(Match.kickoff_time.desc())
        )
        rows = r.all()
        matches_by_team = defaultdict(list)  # team_id -> [(league_id, opp_id, kickoff)]
        for hid, aid, lg, kt in rows:
            if lg in cup_ids:
                continue
            matches_by_team[hid].append((lg, aid, kt))
            matches_by_team[aid].append((lg, hid, kt))

        tr = await db.execute(select(Team))
        teams = {t.id: t for t in tr.scalars().all()}

        def modal(tid):
            """多数票 + 平局取该联赛最近 kickoff"""
            m = matches_by_team.get(tid)
            if not m:
                return None, 0, []
            counter = Counter(lg for lg, _, _ in m)
            kt_of = defaultdict(list)
            for lg, _, kt in m:
                kt_of[lg].append(kt)
            top = max(counter, key=lambda lg: (counter[lg], max(kt_of[lg])))
            return top, sum(counter.values()), m

        changed = []
        unchanged = []
        for tid, t in teams.items():
            top, total, m = modal(tid)
            if top is None:
                continue
            cur = t.league_id
            if top == cur:
                continue
            if total >= 2 or (total == 1 and cur in (None, 6)):
                changed.append((tid, t, cur, top, total, "多数票/污染修复"))
            else:
                # 单票互证：该场比赛对手的多数票联赛 == top
                opp_id = next((o for lg, o, _ in m if lg == top), None)
                opp_top, _, _ = modal(opp_id) if opp_id else (None, 0, [])
                if opp_top == top:
                    changed.append((tid, t, cur, top, total, f"互证(对手{opp_id}={opp_top})"))
                else:
                    unchanged.append((tid, t, cur, top, total, f"对手{opp_id}={opp_top}"))

        p(f"\n== 待更新: {len(changed)} ==")
        for tid, t, cur, top, total, why in sorted(changed, key=lambda x: x[3] == 6, reverse=True):
            p(f"  id={tid} sm={t.sportmonks_id} {t.name_zh}: {cur}({league_name.get(cur,'?')}) -> {top}({league_name.get(top,'?')})  票={total} [{why}]")

        p(f"\n== 有票但未更新（留人检）: {len(unchanged)} ==")
        for tid, t, cur, top, total, why in sorted(unchanged, key=lambda x: x[1].sportmonks_id or 0)[:40]:
            p(f"  id={tid} sm={t.sportmonks_id} {t.name_zh}: 当前={cur}({league_name.get(cur,'?')}) 反推={top}({league_name.get(top,'?')})  票={total} [{why}]")

        if changed:
            p(f"\n== 应用更新（{len(changed)} 条）==")
            for tid, t, cur, top, total, why in changed:
                t.league_id = top
                p(f"  {t.name_zh}({tid}): {cur} -> {top}")
            await db.commit()
            p("  已提交")
        else:
            p("\n无更新")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出已写入: {OUT}")


asyncio.run(main())
