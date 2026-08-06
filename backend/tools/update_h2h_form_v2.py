"""更新周日007-009的H2H和近期状态（简化版）"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import Team, HeadToHead, TeamSeasonStats
from sqlalchemy import select, delete

MATCHES = [
    ("周日007", 159, 167),   # AC奥卢 vs Ilves
    ("周日008", 147, 150),   # AIK索尔纳 vs Örgryte
    ("周日009", 181, 249),   # KFUM Oslo vs Kristiansund
]


async def main():
    sm = SportMonksClient()

    async with async_session() as db:
        for label, tid1, tid2 in MATCHES:
            t1 = (await db.execute(select(Team).where(Team.id == tid1))).scalar_one()
            t2 = (await db.execute(select(Team).where(Team.id == tid2))).scalar_one()
            sm1 = t1.sportmonks_id
            sm2 = t2.sportmonks_id
            print(f"\n--- {label}: {t1.name_zh}(SM={sm1}) vs {t2.name_zh}(SM={sm2}) ---")

            if not sm1 or not sm2:
                print("  跳过：缺少SM ID")
                continue

            # ── H2H ──
            try:
                h2h_data = await sm.get_head_to_head(sm1, sm2)
                print(f"  H2H: {len(h2h_data)} 条")
            except Exception as e:
                print(f"  H2H API失败: {e}")
                h2h_data = []

            if h2h_data:
                # 删除旧数据
                await db.execute(delete(HeadToHead).where(
                    ((HeadToHead.home_team_id == tid1) & (HeadToHead.away_team_id == tid2)) |
                    ((HeadToHead.home_team_id == tid2) & (HeadToHead.away_team_id == tid1))
                ))

                for h in h2h_data:
                    scores = h.get("scores", {})
                    ppts = h.get("participants", [])
                    if len(ppts) < 2:
                        continue
                    p1 = ppts[0] if isinstance(ppts[0], dict) else {}
                    p2 = ppts[1] if isinstance(ppts[1], dict) else {}
                    if p1.get("id") == sm1:
                        db_home, db_away = tid1, tid2
                        hs, aws = scores.get("localteam_score"), scores.get("visitorteam_score")
                    else:
                        db_home, db_away = tid2, tid1
                        hs, aws = scores.get("visitorteam_score"), scores.get("localteam_score")

                    league = h.get("league", {}) or {}
                    db.add(HeadToHead(
                        home_team_id=db_home, away_team_id=db_away,
                        match_date=h.get("starting_at"),
                        home_score=hs, away_score=aws,
                        competition_name=league.get("name", "") if isinstance(league, dict) else "",
                        sportmonks_fixture_id=h.get("id"),
                    ))
                await db.flush()
                print(f"    写入完成")

            # ── Form ──
            for tid in [tid1, tid2]:
                t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one()
                if not t.sportmonks_id:
                    continue
                try:
                    td = await sm.get_team_by_id(t.sportmonks_id, includes="latest;latest.participants;latest.scores")
                except Exception as e:
                    print(f"  Form {t.name_zh}: API失败 {e}")
                    continue

                latest = td.get("latest", [])
                if not latest:
                    print(f"  Form {t.name_zh}: 无数据")
                    continue

                form_chars = []
                recent = []
                for m in latest[:10]:
                    scores = m.get("scores", {})
                    ppts = m.get("participants", [])
                    if len(ppts) < 2:
                        continue
                    p1 = ppts[0] if isinstance(ppts[0], dict) else {}
                    hs = scores.get("localteam_score")
                    aws = scores.get("visitorteam_score")
                    if p1.get("id") == t.sportmonks_id:
                        my, opp = hs, aws
                        opp_name = (ppts[1] if isinstance(ppts[1], dict) else {}).get("name", "")
                        venue = "H"
                    else:
                        my, opp = aws, hs
                        opp_name = p1.get("name", "")
                        venue = "A"
                    if my is not None and opp is not None:
                        if my > opp: r = "W"
                        elif my == opp: r = "D"
                        else: r = "L"
                        form_chars.append(r)
                    else:
                        r = "?"
                    recent.append({"opponent": opp_name, "score": f"{my}-{opp}" if my is not None else "?-?",
                                   "result": r, "date": str(m.get("starting_at", ""))[:10], "venue": venue})

                exist = await db.execute(select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid).order_by(TeamSeasonStats.id.desc()).limit(1))
                stats = exist.scalar_one_or_none()
                if not stats:
                    stats = TeamSeasonStats(team_id=tid, season="latest")
                    db.add(stats)
                    await db.flush()
                if form_chars:
                    stats.form = "".join(form_chars)
                if recent:
                    stats.recent_matches = recent
                print(f"  Form {t.name_zh}: {''.join(form_chars) if form_chars else '?'} ({len(recent)}场)")

            await db.commit()

    await sm.close()
    print("\n完成!")

asyncio.run(main())
