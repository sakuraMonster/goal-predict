"""一次性确定性修复 v2：6 场未匹配赛事
- 比赛匹配到已验证的 SM fixture
- 球队：占位球队(UNKNOWN) → 合并到 teams 表现存的正确记录（按 SM ID 反查，存在则改引用+删占位）
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

from datetime import datetime, timedelta
from app.db.database import async_session
from app.db.models import Match, Team, TeamAlias
from app.collector.sportmonks.client import SportMonksClient
from sqlalchemy import select, func

FIXES = {
    "2040829": 19766303,
    "2040830": 19766301,
    "2040818": 19712157,
    "2040831": 19709410,
    "2040819": 19712162,
    "2040820": 19712161,
}


async def _count_refs(db, team_id: int) -> int:
    r = await db.execute(select(func.count()).select_from(Match).where(
        (Match.home_team_id == team_id) | (Match.away_team_id == team_id)))
    return r.scalar()


async def main():
    sm = SportMonksClient()
    async with async_session() as db:
        to_delete = []          # 占位球队 id 列表
        for jc, fx_id in FIXES.items():
            fx = await sm.get_fixture_by_id(fx_id, includes="participants")
            ps = {p.get("id"): p for p in fx.get("participants", []) if isinstance(p, dict)}
            r = await db.execute(select(Match).where(Match.jc_match_id == jc))
            m = r.scalar_one_or_none()
            if not m:
                print(f"✗ {jc} 比赛不存在"); continue
            if m.sportmonks_fixture_id:
                print(f"· {jc} 已匹配 fx={m.sportmonks_fixture_id}，跳过"); continue

            # 时间校验 ±3h
            fx_dt = datetime.strptime(fx.get("starting_at", ""), "%Y-%m-%d %H:%M:%S")
            kickoff_utc = m.kickoff_time.replace(tzinfo=None) - timedelta(hours=8)
            delta = abs((fx_dt - kickoff_utc).total_seconds()) / 3600
            if delta > 3:
                print(f"✗ {jc} 时间不符: fx={fx.get('starting_at')} vs {kickoff_utc} ({delta:.1f}h)"); continue

            home_p = next((p for p in ps.values() if (p.get("meta") or {}).get("location") == "home"), None)
            away_p = next((p for p in ps.values() if (p.get("meta") or {}).get("location") == "away"), None)
            if not home_p or not away_p:
                print(f"✗ {jc} 无 location"); continue

            m.sportmonks_fixture_id = fx_id
            print(f"✓ {jc} {m.home_team_name} vs {m.away_team_name} → fx={fx_id}")

            # 逐侧处理：占位 → 现存合并；否则写反推值
            for label, participant, side_tid in [
                ("主", home_p, m.home_team_id),
                ("客", away_p, m.away_team_id),
            ]:
                if not side_tid:
                    print(f"  {label}侧无 team_id，跳过"); continue
                t = await db.get(Team, side_tid)
                if not t:
                    continue
                sm_id = participant.get("id")
                sm_name = participant.get("name", "")
                # 查找现存同 SM ID 球队
                r2 = await db.execute(select(Team).where(Team.sportmonks_id == sm_id))
                canonical = r2.scalar_one_or_none()
                if canonical and canonical.id != t.id:
                    # 合并：比赛引用 → 现存，占位删除
                    old = f"id={t.id} {t.name_zh!r}"
                    if label == "主":
                        m.home_team_id = canonical.id
                    else:
                        m.away_team_id = canonical.id
                    if t.id not in to_delete:
                        to_delete.append(t.id)
                    print(f"   {label}队 占位{old} → 合并到 id={canonical.id} {canonical.name_zh!r} (sm={sm_id} {sm_name})")
                elif canonical:
                    print(f"   {label}队 id={t.id} 已是正确记录")
                else:
                    # 无现存：直接写占位
                    old = f"sm={t.sportmonks_id} en={t.name_en!r}"
                    t.sportmonks_id = sm_id
                    t.name_en = sm_name
                    t.needs_review = False
                    t.review_reason = None
                    if participant.get("image_path") and not t.logo_url:
                        t.logo_url = participant["image_path"]
                    print(f"   {label}队 {t.name_zh}: {old} → sm={sm_id} en={sm_name!r}")

        # 删除占位球队（Core 语句直接删，避免 ORM unitofwork 顺序导致外键冲突）
        for tid in to_delete:
            refs = await _count_refs(db, tid)
            if refs > 0:
                print(f"✗ 占位 id={tid} 仍被 {refs} 场引用，不删除")
                continue
            ar = await db.execute(select(TeamAlias).where(TeamAlias.team_id == tid))
            aliases = list(ar.scalars().all())
            for a in aliases:
                print(f"    清理占位别名: {a.alias_name!r} (source={a.source})")
            await db.execute(TeamAlias.__table__.delete().where(TeamAlias.team_id == tid))
            await db.execute(Team.__table__.delete().where(Team.id == tid))
            print(f"  删除占位球队 id={tid}")

        await db.commit()
        print("\n提交完成")

        print("\n== 验证 ==")
        for jc, fx_id in FIXES.items():
            r = await db.execute(select(Match).where(Match.jc_match_id == jc))
            m = r.scalar_one_or_none()
            if m:
                h, a = m.home_team, m.away_team
                print(f"  {jc} {m.home_team_name} vs {m.away_team_name}: fx={m.sportmonks_fixture_id} "
                      f"| home id={m.home_team_id} sm={h.sportmonks_id if h else None} en={h.name_en if h else '?'} "
                      f"| away id={m.away_team_id} sm={a.sportmonks_id if a else None} en={a.name_en if a else '?'}")
    await sm.close()


if __name__ == "__main__":
    asyncio.run(main())
