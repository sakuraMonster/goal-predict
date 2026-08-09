"""修复布拉加匹配 + 拉取H2H和近期状态"""
import asyncio, sys, os, json
sys.path.insert(0, "e:/zhangxuejun/new-thinking/ricking-03/backend")
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(env_path)
from sqlalchemy import select, text
from app.db.database import async_session
from app.db.models import Match, Team, TeamAlias
from app.collector.sportmonks.client import SportMonksClient
from datetime import datetime

# 常量
MOREIRENSE_DB_ID = 401   # DB: Moreirense (SM=1085)
BRAGA_DB_ID = 408        # DB: Sporting Braga (SM=884)
FIXTURE_SM_ID = 19736853  # Moreirense vs Sporting Braga
MATCH_ID = 15575

async def main():
    sm = SportMonksClient()

    async with async_session() as db:
        # ── 1. 修复错误的摩雷伦斯 (ID=359, 错误映射到 Morecambe SM 772) ──
        print("=== 1. 修复摩雷伦斯映射 ===")
        team_359 = await db.get(Team, 359)
        if team_359:
            print(f"  ID=359: {team_359.name_zh} / {team_359.name_en} / sm={team_359.sportmonks_id}")
            team_359.sportmonks_id = None  # 清除错误的 SM 映射
            team_359.name_en = "Moreirense (dup, use 401)"
            team_359.needs_review = True
            team_359.review_reason = "SM 772(Morecambe)错误，已清除。正确记录: ID=401 Moreirense(SM=1085)"
            print(f"  已清除 SM 映射")

        # ── 2. 为布拉加创建别名 → Sporting Braga (ID=408) ──
        print("\n=== 2. 创建布拉加别名 ===")
        r = await db.execute(
            select(TeamAlias).where(
                TeamAlias.team_id == BRAGA_DB_ID,
                TeamAlias.alias_name == '布拉加'
            )
        )
        if not r.scalar_one_or_none():
            db.add(TeamAlias(
                team_id=BRAGA_DB_ID, alias_name='布拉加',
                source='sporttery.cn', is_primary=True,
                league_name_zh='葡超'
            ))
            print(f"  创建: 布拉加 → team_id={BRAGA_DB_ID} (Sporting Braga)")
        else:
            print(f"  别名已存在")

        # ── 3. 更新比赛: home=401(Moreirense), away=408(Braga) ──
        print(f"\n=== 3. 更新比赛 {MATCH_ID} ===")
        match = await db.get(Match, MATCH_ID)
        if match:
            print(f"  当前: {match.home_team_name}(tid={match.home_team_id}) vs {match.away_team_name}(tid={match.away_team_id})")
            match.home_team_id = MOREIRENSE_DB_ID
            match.away_team_id = BRAGA_DB_ID
            match.sportmonks_fixture_id = FIXTURE_SM_ID
            print(f"  更新: home={MOREIRENSE_DB_ID}(Moreirense), away={BRAGA_DB_ID}(Braga), fixture={FIXTURE_SM_ID}")

        # ── 4. 清理占位球队 1702 ──
        print("\n=== 4. 清理占位 ===")
        ph = await db.get(Team, 1702)
        if ph:
            ph.needs_review = False
            ph.review_reason = "已合并到 Sporting Braga (id=408, SM=884)"
            print(f"  占位 1702 已标记合并")

        await db.commit()
        print("  DB 变更已提交\n")

    # ══════════════════════════════════════
    # ── 5. H2H: Moreirense vs Braga ──
    # ══════════════════════════════════════
    print("=== 5. 拉取 H2H ===")
    try:
        h2h_data = await sm.get_head_to_head(1085, 884)  # SM ids
        print(f"  API 返回: {len(h2h_data)} 条")
    except Exception as e:
        print(f"  API 失败: {e}")
        h2h_data = []

    if h2h_data:
        async with async_session() as db:
            await db.execute(text(
                "DELETE FROM head_to_head WHERE (home_team_id=:a AND away_team_id=:b) OR (home_team_id=:b AND away_team_id=:a)",
            ), {"a": MOREIRENSE_DB_ID, "b": BRAGA_DB_ID})

            for h in h2h_data:
                scores_raw = h.get("scores", [])
                # SM H2H 返回 scores 是 list: [{'score': {'goals': N, 'participant': 'home'}, 'description': 'CURRENT'}, ...]
                home_g = away_g = None
                if isinstance(scores_raw, list):
                    for sc in scores_raw:
                        desc = sc.get("description", "")
                        if desc in ("CURRENT", "FT"):
                            s = sc.get("score", {})
                            p = s.get("participant", "")
                            g = s.get("goals")
                            if p == "home": home_g = int(g) if g is not None else None
                            elif p == "away": away_g = int(g) if g is not None else None

                ppts = h.get("participants", [])
                if len(ppts) < 2:
                    continue
                p1_id = ppts[0].get("id") if isinstance(ppts[0], dict) else None

                if p1_id == 1085:
                    db_h, db_a, hgs, ags = MOREIRENSE_DB_ID, BRAGA_DB_ID, home_g, away_g
                else:
                    db_h, db_a, hgs, ags = BRAGA_DB_ID, MOREIRENSE_DB_ID, away_g, home_g

                league = h.get("league", {}) or {}
                lg_name = league.get("name", "") if isinstance(league, dict) else ""
                date_str = h.get("starting_at", "")
                match_dt = datetime(2000, 1, 1)  # fallback
                if date_str:
                    try:
                        match_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    except:
                        try:
                            match_dt = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
                        except:
                            pass

                await db.execute(text("""
                    INSERT INTO head_to_head (home_team_id, away_team_id, match_date, home_score, away_score, competition, sportmonks_fixture_id)
                    VALUES (:hid, :aid, :md, :hs, :aws, :cn, :fx)
                """), {"hid": db_h, "aid": db_a, "md": match_dt,
                       "hs": hgs, "aws": ags, "cn": lg_name, "fx": h.get("id")})

            await db.commit()
            print(f"  H2H 写入完成\n")

    # ══════════════════════════════════════
    # ── 6. Form: Braga + Moreirense ──
    # ══════════════════════════════════════
    print("=== 6. 拉取近期状态 ===")
    FORM_TEAMS = [(BRAGA_DB_ID, "Braga", 884), (MOREIRENSE_DB_ID, "Moreirense", 1085)]

    for tid, tname, sm_id in FORM_TEAMS:
        print(f"\n  [{tname}]")
        try:
            td = await sm.get_team_by_id(sm_id, includes="latest;latest.participants;latest.scores")
        except Exception as e:
            print(f"  API 失败: {e}")
            continue

        latest = td.get("latest", [])
        if not latest:
            print(f"  无 latest 数据")
            continue

        form_chars, recent = [], []
        for m in latest[:10]:
            scores_raw = m.get("scores", [])
            # latest fixtures 的 scores 也是 list 格式
            home_g = away_g = None
            if isinstance(scores_raw, list):
                for sc in scores_raw:
                    desc = sc.get("description", "")
                    if desc in ("CURRENT", "FT"):
                        s = sc.get("score", {})
                        p = s.get("participant", "")
                        g = s.get("goals")
                        if p == "home": home_g = int(g) if g is not None else None
                        elif p == "away": away_g = int(g) if g is not None else None

            ppts = m.get("participants", [])
            if len(ppts) < 2:
                continue
            p1 = ppts[0] if isinstance(ppts[0], dict) else {}

            if p1.get("id") == sm_id:
                my, opp, venue = home_g, away_g, "H"
                opp_p = ppts[1] if isinstance(ppts[1], dict) else {}
            else:
                my, opp, venue = away_g, home_g, "A"
                opp_p = p1
            opp_name = opp_p.get("name", "")

            r = "W" if (my is not None and opp is not None and my > opp) else \
                ("D" if (my is not None and opp is not None and my == opp) else \
                 ("L" if (my is not None and opp is not None and my < opp) else "?"))
            form_chars.append(r)
            recent.append({
                "opponent": opp_name,
                "score": f"{my}-{opp}" if my is not None else "?-?",
                "result": r, "date": str(m.get("starting_at", ""))[:10], "venue": venue
            })

        form_str = "".join(form_chars)
        print(f"  form={form_str} ({len(recent)}场)")
        for rec in recent[:5]:
            print(f"    {rec['venue']} {rec['date']}: {rec['opponent']} {rec['score']} {rec['result']}")

        recent_json = json.dumps(recent, ensure_ascii=False)
        async with async_session() as db:
            await db.execute(text("""
                INSERT INTO team_season_stats (team_id, season, form, recent_matches)
                VALUES (:tid, 'latest', :form, CAST(:recent AS jsonb))
                ON CONFLICT DO NOTHING
            """), {"tid": tid, "form": form_str, "recent": recent_json})
            await db.execute(text("""
                UPDATE team_season_stats SET form=:form, recent_matches=CAST(:recent AS jsonb)
                WHERE team_id=:tid AND season='latest'
            """), {"tid": tid, "form": form_str, "recent": recent_json})
            await db.commit()
            print(f"  Form 写入完成")

    await sm.close()
    print("\n" + "=" * 50)
    print("全部完成!")
    print(f"  - Moreirense: ID={MOREIRENSE_DB_ID} (SM=1085)")
    print(f"  - Braga: ID={BRAGA_DB_ID} (SM=884, 别名=布拉加)")
    print(f"  - 比赛 {MATCH_ID}: fixture={FIXTURE_SM_ID}")

asyncio.run(main())
