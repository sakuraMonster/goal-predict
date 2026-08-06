"""最简单的H2H+Form更新：直接用SM API拉取并SQL更新"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()
from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import HeadToHead, TeamSeasonStats, Team
from sqlalchemy import select, text

PAIRS = [(159, 167), (147, 150), (181, 249)]  # (tid1, tid2)
TEAMS = [159, 167, 147, 150, 181, 249]


async def main():
    sm = SportMonksClient()

    async with async_session() as db:
        # ── 1. H2H ──
        for tid1, tid2 in PAIRS:
            t1 = (await db.execute(select(Team).where(Team.id == tid1))).scalar_one()
            t2 = (await db.execute(select(Team).where(Team.id == tid2))).scalar_one()
            sm1, sm2 = t1.sportmonks_id, t2.sportmonks_id
            print(f"H2H: {t1.name_zh}({sm1}) vs {t2.name_zh}({sm2})")
            if not sm1 or not sm2:
                continue

            try:
                data = await sm.get_head_to_head(sm1, sm2)
                print(f"  API: {len(data)} 条")
            except Exception as e:
                print(f"  API失败: {e}")
                continue

            # 直接用SQL upsert，避免ORM字段问题
            for h in data:
                scores = h.get("scores", {})
                ppts = h.get("participants", [])
                if len(ppts) < 2:
                    continue
                p1_id = ppts[0].get("id") if isinstance(ppts[0], dict) else None
                hs = scores.get("localteam_score")
                aws = scores.get("visitorteam_score")
                if p1_id == sm1:
                    db_h, db_a, hgs, ags = tid1, tid2, hs, aws
                else:
                    db_h, db_a, hgs, ags = tid2, tid1, aws, hs

                league = h.get("league", {}) or {}
                lg_name = league.get("name", "") if isinstance(league, dict) else ""
                match_date = h.get("starting_at")
                fx_id = h.get("id")

                # upsert: delete old, insert new
                await db.execute(text("""
                    INSERT INTO head_to_head (home_team_id, away_team_id, match_date, home_score, away_score, competition_name, sportmonks_fixture_id)
                    VALUES (:hid, :aid, :md, :hs, :aws, :cn, :fx)
                    ON CONFLICT (sportmonks_fixture_id) DO UPDATE SET
                        home_team_id=EXCLUDED.home_team_id, away_team_id=EXCLUDED.away_team_id,
                        match_date=EXCLUDED.match_date, home_score=EXCLUDED.home_score,
                        away_score=EXCLUDED.away_score, competition_name=EXCLUDED.competition_name
                """), {"hid": db_h, "aid": db_a, "md": match_date, "hs": hgs, "aws": ags, "cn": lg_name, "fx": fx_id})
            await db.commit()
            print(f"    写入完成")

        # ── 2. Form ──
        for tid in TEAMS:
            t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one()
            sm_id = t.sportmonks_id
            if not sm_id:
                continue
            print(f"Form: {t.name_zh}(SM={sm_id})")
            try:
                td = await sm.get_team_by_id(sm_id, includes="latest;latest.participants;latest.scores")
            except Exception as e:
                print(f"  API失败: {e}")
                continue

            latest = td.get("latest", [])
            if not latest:
                print(f"  无数据")
                continue

            form_chars = []
            recent = []
            for m in latest[:10]:
                scores = m.get("scores", {})
                ppts = m.get("participants", [])
                if len(ppts) < 2:
                    continue
                p1 = ppts[0] if isinstance(ppts[0], dict) else {}
                hs2, aws2 = scores.get("localteam_score"), scores.get("visitorteam_score")
                if p1.get("id") == sm_id:
                    my, opp = hs2, aws2
                    opp_name = (ppts[1] if isinstance(ppts[1], dict) else {}).get("name", "")
                    venue = "H"
                else:
                    my, opp = aws2, hs2
                    opp_name = p1.get("name", "")
                    venue = "A"
                if my is not None and opp is not None:
                    r = "W" if my > opp else ("D" if my == opp else "L")
                else:
                    r = "?"
                form_chars.append(r)
                recent.append({"opponent": opp_name, "score": f"{my}-{opp}" if my is not None else "?-?",
                               "result": r, "date": str(m.get("starting_at", ""))[:10], "venue": venue})

            form_str = "".join(form_chars)
            recent_json = json.dumps(recent, ensure_ascii=False)
            await db.execute(text("""
                INSERT INTO team_season_stats (team_id, season, form, recent_matches)
                VALUES (:tid, 'latest', :form, :recent::jsonb)
                ON CONFLICT DO NOTHING
            """), {"tid": tid, "form": form_str, "recent": recent_json})
            # 如果已存在，更新
            await db.execute(text("""
                UPDATE team_season_stats SET form=:form, recent_matches=:recent::jsonb
                WHERE team_id=:tid AND season='latest'
            """), {"tid": tid, "form": form_str, "recent": recent_json})
            await db.commit()
            print(f"  form={form_str} ({len(recent)}场)")

    await sm.close()
    print("\n完成!")

asyncio.run(main())
