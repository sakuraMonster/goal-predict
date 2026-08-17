"""t2 修复 v2：重复球队合并（新条目 -> 老条目，老条目 sm 正确且有完整数据）

对:
  (1678 阿尔维卡 -> 402 AVS),      (208 卡萨皮亚 -> 424 Casa Pia),
  (1646 马里迪莫 -> 1683 Marítimo),(1662 阿马多拉 -> 426 Estrela Amadora),
  (1680 里奥阿维 -> 430 Rio Ave),  (1651 阿罗卡 -> 413 Arouca)

步骤（每个新条目）:
1. matches.home/away_team_id 迁移
2. head_to_head home/away 迁移（冲突自引用行删除）
3. injuries.team_id 迁移
4. team_aliases.team_id 迁移
5. team_season_stats.team_id 迁移（如有剩余）
6. 老条目 league_id 若为 6（污染）且新条目=21 -> 修正；name_zh 取中文名
7. 删除新条目 Team
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, update, delete
from app.db.database import async_session
from app.db.models import Team, Match, TeamAlias, TeamSeasonStats, HeadToHead, Injury

MERGES = [(1678, 402), (208, 424), (1646, 1683), (1662, 426), (1680, 430), (1651, 413)]
OUT = os.path.join(os.path.dirname(__file__), "t2_merge_result.txt")


async def main():
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        for new_id, old_id in MERGES:
            new_t = (await db.execute(select(Team).where(Team.id == new_id))).scalar_one_or_none()
            old_t = (await db.execute(select(Team).where(Team.id == old_id))).scalar_one_or_none()
            if not new_t or not old_t:
                p(f"!! {new_id}->{old_id} 条目缺失，跳过")
                continue
            p(f"\n===== {new_t.name_zh}({new_id}, sm={new_t.sportmonks_id}) -> {old_t.name_zh}({old_id}, sm={old_t.sportmonks_id}) =====")

            # 1. matches
            r = await db.execute(update(Match).where(Match.home_team_id == new_id).values(home_team_id=old_id))
            r2 = await db.execute(update(Match).where(Match.away_team_id == new_id).values(away_team_id=old_id))
            p(f"  matches: home {r.rowcount} 场, away {r2.rowcount} 场")

            # 2. head_to_head
            try:
                r = await db.execute(update(HeadToHead).where(HeadToHead.home_team_id == new_id).values(home_team_id=old_id))
                r2 = await db.execute(update(HeadToHead).where(HeadToHead.away_team_id == new_id).values(away_team_id=old_id))
                if r.rowcount or r2.rowcount:
                    p(f"  head_to_head: home {r.rowcount} 条, away {r2.rowcount} 条")
                # 自引用清理
                del_r = await db.execute(delete(HeadToHead).where(HeadToHead.home_team_id == old_id, HeadToHead.away_team_id == old_id))
                if del_r.rowcount:
                    p(f"  head_to_head 自引用删除 {del_r.rowcount} 条")
            except Exception as e:
                p(f"  head_to_head 错误: {e}")

            # 3. injuries
            try:
                r = await db.execute(update(Injury).where(Injury.team_id == new_id).values(team_id=old_id))
                if r.rowcount:
                    p(f"  injuries: {r.rowcount} 条")
            except Exception as e:
                p(f"  injuries 错误: {e}")

            # 4. team_aliases
            try:
                r = await db.execute(update(TeamAlias).where(TeamAlias.team_id == new_id).values(team_id=old_id))
                if r.rowcount:
                    p(f"  team_aliases: {r.rowcount} 条")
            except Exception as e:
                p(f"  team_aliases 错误: {e}")

            # 5. team_season_stats
            r = await db.execute(update(TeamSeasonStats).where(TeamSeasonStats.team_id == new_id).values(team_id=old_id))
            if r.rowcount:
                p(f"  team_season_stats: {r.rowcount} 条")

            # 6. 老条目信息合并
            if old_t.league_id in (None, 6) and new_t.league_id not in (None, 6):
                p(f"  league_id: {old_t.league_id} -> {new_t.league_id}")
                old_t.league_id = new_t.league_id
            if new_t.name_zh and (not old_t.name_zh or old_t.name_zh == old_t.name_en):
                old_t.name_zh = new_t.name_zh
                p(f"  name_zh -> {new_t.name_zh}")
            if new_t.logo_url and not old_t.logo_url:
                old_t.logo_url = new_t.logo_url

            # 7. 删除新条目
            await db.delete(new_t)
            p(f"  已删除 {new_id}")

        await db.commit()
        p("\n已提交")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出已写入: {OUT}")


asyncio.run(main())
