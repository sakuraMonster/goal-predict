"""t2 修复：数据采集缺口（sportmonks_id 映射错误 + 波尔图重复 + 垃圾 stats + league_id 污染）

1. 修正 6 队错误 sm_id（已通过 SportMonks API 搜索验证）：
   1678 阿尔维卡: 1822 -> 269225 (AVS)
   208  卡萨皮亚: 292  -> 11741  (Casa Pia)
   1646 马里迪莫: 255  -> 5931   (Marítimo)
   1662 阿马多拉: 500  -> 12152  (Estrela Amadora)
   1680 里奥阿维: 172  -> 6377   (Rio Ave)
   1651 阿罗卡:   230547 -> 4092 (Arouca)
2. 波尔图重复合并：1299(错误sm=1498) -> 486(正确sm=652)，迁移 matches 引用后删除 1299
3. 删除基于错误 sm 拉取的垃圾 TeamSeasonStats
4. 修正"当前竞彩比赛涉及球队"的 TeamSeasonStats.league_id（污染值 6 -> 真实联赛）
"""
import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, update
from app.db.database import async_session
from app.db.models import Team, TeamSeasonStats, Match, TeamAlias

# 已验证的 sm_id 修正映射
SM_FIX = {
    1678: 269225,   # 阿尔维卡 AVS
    208: 11741,     # 卡萨皮亚 Casa Pia
    1646: 5931,     # 马里迪莫 Marítimo
    1662: 12152,    # 阿马多拉 Estrela Amadora
    1680: 6377,     # 里奥阿维 Rio Ave
    1651: 4092,     # 阿罗卡 Arouca
}
PORTO_OLD, PORTO_NEW = 1299, 486  # 合并: 1299 -> 486
OUT = os.path.join(os.path.dirname(__file__), "t2_fix_result.txt")


async def main():
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        # ── 1. sm_id 修正 ──
        p("== 1. sm_id 修正 ==")
        for tid, new_sm in SM_FIX.items():
            t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one()
            old_sm = t.sportmonks_id
            # 检查 new_sm 是否已被其他球队占用
            dup = (await db.execute(select(Team).where(Team.sportmonks_id == new_sm))).scalar_one_or_none()
            if dup:
                p(f"  警告: {t.name_zh}({tid}) 新sm={new_sm} 已被 {dup.name_zh}({dup.id}) 占用，跳过")
                continue
            t.sportmonks_id = new_sm
            p(f"  {t.name_zh}({tid}): sm {old_sm} -> {new_sm}")

        # ── 2. 波尔图合并 1299 -> 486 ──
        p("\n== 2. 波尔图重复合并 ==")
        old_t = (await db.execute(select(Team).where(Team.id == PORTO_OLD))).scalar_one()
        new_t = (await db.execute(select(Team).where(Team.id == PORTO_NEW))).scalar_one()
        p(f"  {old_t.name_zh}({PORTO_OLD}, sm={old_t.sportmonks_id}) -> {new_t.name_zh}({PORTO_NEW}, sm={new_t.sportmonks_id})")

        # matches 引用迁移
        r1 = await db.execute(update(Match).where(Match.home_team_id == PORTO_OLD).values(home_team_id=PORTO_NEW))
        r2 = await db.execute(update(Match).where(Match.away_team_id == PORTO_OLD).values(away_team_id=PORTO_NEW))
        p(f"  matches: home {r1.rowcount} 场, away {r2.rowcount} 场 迁移")

        # TeamSeasonStats: 1299 的垃圾记录删除（Potters Bar Town 数据）
        r3 = await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id == PORTO_OLD))
        del_stats = r3.scalars().all()
        for s in del_stats:
            await db.delete(s)
        p(f"  删除 {PORTO_OLD} 的垃圾 TeamSeasonStats {len(del_stats)} 条")

        # TeamAlias 迁移
        r4 = await db.execute(update(TeamAlias).where(TeamAlias.team_id == PORTO_OLD).values(team_id=PORTO_NEW))
        if r4.rowcount:
            p(f"  TeamAlias 迁移 {r4.rowcount} 条")

        # 更新 486 中文名
        if not new_t.name_zh or new_t.name_zh == "Porto":
            new_t.name_zh = "波尔图"
            p(f"  486.name_zh -> {new_t.name_zh}")

        # 删除 1299
        await db.delete(old_t)
        p(f"  已删除 Team {PORTO_OLD}")

        # ── 3. 删除 6 队垃圾 TeamSeasonStats（全部基于错误 sm）──
        p("\n== 3. 垃圾 TeamSeasonStats 清理 ==")
        for tid in SM_FIX:
            rr = await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid))
            stats = rr.scalars().all()
            for s in stats:
                await db.delete(s)
            p(f"  删除 {tid} 的 TeamSeasonStats {len(stats)} 条")

        # ── 4. 修正当前竞彩比赛球队的 TeamSeasonStats.league_id ──
        p("\n== 4. TeamSeasonStats.league_id 修正（污染值6 -> 真实联赛）==")
        # 当前竞彩比赛涉及球队
        r = await db.execute(select(Match.home_team_id, Match.away_team_id).where(Match.kickoff_time >= datetime(2026, 7, 1, 0, 0)))
        involved = set()
        for h, a in r.all():
            if h: involved.add(h)
            if a: involved.add(a)
        p(f"  涉及球队数: {len(involved)}")

        fixed = 0
        by_league = defaultdict(int)
        tr = await db.execute(select(Team.id, Team.league_id).where(Team.id.in_(involved), Team.league_id.isnot(None)))
        team_league = {tid: lg for tid, lg in tr.all()}
        for tid, real_lg in team_league.items():
            if real_lg == 6:
                continue  # 韩K真实归属，或未修正球队，跳过
            rr = await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid))
            for s in rr.scalars().all():
                if s.league_id == 6 and s.league_id != real_lg:
                    s.league_id = real_lg
                    fixed += 1
                    by_league[real_lg] += 1
        p(f"  修正 {fixed} 条 stats 记录 league_id")
        for lg, cnt in sorted(by_league.items()):
            p(f"    league {lg}: {cnt} 条")

        await db.commit()
        p("\n已提交")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出已写入: {OUT}")


asyncio.run(main())
