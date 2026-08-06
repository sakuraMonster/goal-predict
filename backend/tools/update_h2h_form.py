"""更新周一002-003的H2H和近期状态"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv; load_dotenv()

from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from app.db.models import Team, HeadToHead, TeamSeasonStats
from sqlalchemy import select, delete
from datetime import datetime


MATCHES = [
    ("周一002", 15503, 50, 158),    # 哈尔姆斯塔德 vs 天狼星
    ("周一003", 15504, 155, 1597),  # 佐加顿斯 vs 韦斯特罗斯
]

H2H_STAT_CODES = {
    "shots-total": "shots", "shots-on-target": "shots_on_target",
    "shots-off-target": "shots_off", "shots-blocked": "shots_blocked",
    "ball-possession": "possession", "corners": "corners",
    "fouls": "fouls", "attacks": "attacks", "dangerous-attacks": "dangerous",
}


async def main():
    sm = SportMonksClient()

    async with async_session() as db:
        for label, mid, tid1, tid2 in MATCHES:
            t1 = (await db.execute(select(Team).where(Team.id == tid1))).scalar_one()
            t2 = (await db.execute(select(Team).where(Team.id == tid2))).scalar_one()
            sm1 = t1.sportmonks_id
            sm2 = t2.sportmonks_id
            print(f"\n{'='*60}")
            print(f"{label} (ID={mid}): {t1.name_zh}(SM={sm1}) vs {t2.name_zh}(SM={sm2})")
            print("="*60)

            if not sm1 or not sm2:
                print("  跳过：缺少SM ID")
                continue

            # ── 1. H2H ──
            print("  H2H...")
            h2h_data = await sm.get_head_to_head(sm1, sm2)
            print(f"    SM API 返回 {len(h2h_data)} 条")

            # 删除旧的
            await db.execute(
                delete(HeadToHead).where(
                    ((HeadToHead.home_team_id == tid1) & (HeadToHead.away_team_id == tid2)) |
                    ((HeadToHead.home_team_id == tid2) & (HeadToHead.away_team_id == tid1))
                )
            )
            await db.flush()

            new_h2h = 0
            for h in h2h_data:
                h_id = h.get("id")
                participants = h.get("participants", [])
                scores_raw = h.get("scores", {})
                stats = h.get("statistics", [])

                home_p = participants[0] if len(participants) > 0 and isinstance(participants[0], dict) else {}
                away_p = participants[1] if len(participants) > 1 and isinstance(participants[1], dict) else {}

                # 确定方向
                home_sm = home_p.get("id")
                away_sm = away_p.get("id")

                # 兼容 scores 的两种格式
                home_score = None
                away_score = None
                if isinstance(scores_raw, dict):
                    if home_sm == sm1:
                        home_score = scores_raw.get("localteam_score")
                        away_score = scores_raw.get("visitorteam_score")
                    else:
                        home_score = scores_raw.get("visitorteam_score")
                        away_score = scores_raw.get("localteam_score")
                elif isinstance(scores_raw, list):
                    for s in scores_raw:
                        if not isinstance(s, dict):
                            continue
                        sc = s.get("score", {})
                        participant = sc.get("participant", "")
                        goals = sc.get("goals")
                        if s.get("description") == "CURRENT" or (home_score is None and away_score is None):
                            if participant == "home":
                                home_score = goals
                            elif participant == "away":
                                away_score = goals

                if home_sm == sm1:
                    db_home, db_away = tid1, tid2
                else:
                    db_home, db_away = tid2, tid1
                    # swap scores if needed
                    if isinstance(scores_raw, list):
                        home_score, away_score = away_score, home_score

                # 提取统计数据
                home_stats, away_stats = {}, {}
                for st in stats:
                    if not isinstance(st, dict):
                        continue
                    code = st.get("type", {}).get("code", "") if isinstance(st.get("type"), dict) else ""
                    team_id = st.get("team_id", "")
                    val = st.get("data", {}).get("value")
                    field = H2H_STAT_CODES.get(code)
                    if field and val is not None:
                        if str(team_id) == str(home_sm):
                            home_stats[field] = val
                        elif str(team_id) == str(away_sm):
                            away_stats[field] = val

                h2h = HeadToHead(
                    home_team_id=db_home, away_team_id=db_away,
                    match_date=datetime.fromisoformat(h.get("starting_at", "").replace("Z", "+00:00")) if h.get("starting_at") else None,
                    home_score=home_score, away_score=away_score,
                    competition=(h.get("league", {}) or {}).get("name", "") if isinstance(h.get("league"), dict) else "",
                    sportmonks_fixture_id=h_id,
                    home_stats=home_stats if home_stats else None,
                    away_stats=away_stats if away_stats else None,
                )
                db.add(h2h)
                new_h2h += 1

            await db.flush()
            print(f"    写入 {new_h2h} 条H2H")

            # ── 2. 近期状态 ──
            for tid in [tid1, tid2]:
                t = (await db.execute(select(Team).where(Team.id == tid))).scalar_one()
                if not t.sportmonks_id:
                    continue

                print(f"  Form: {t.name_zh}...")
                try:
                    team_data = await sm.get_team_by_id(t.sportmonks_id, includes="latest;latest.participants;latest.scores")
                except Exception as e:
                    print(f"    API失败: {e}")
                    continue

                latest = team_data.get("latest", [])
                if not latest:
                    print(f"    无近期比赛")
                    continue

                # 只取最近10场
                recent = []
                form_chars = []
                for m in latest[:10]:
                    ppts = m.get("participants", [])
                    scores_raw = m.get("scores", {})
                    if len(ppts) < 2:
                        continue
                    home_p = ppts[0] if isinstance(ppts[0], dict) else {}
                    away_p = ppts[1] if isinstance(ppts[1], dict) else {}
                    ref_sm_id = t.sportmonks_id

                    # 兼容 scores 的两种格式：
                    # 旧格式: {"localteam_score": 2, "visitorteam_score": 1}
                    # 新格式: [{"score": {"goals": 2, "participant": "home"}, "description": "CURRENT"}, ...]
                    home_score = None
                    away_score = None
                    if isinstance(scores_raw, dict):
                        home_score = scores_raw.get("localteam_score")
                        away_score = scores_raw.get("visitorteam_score")
                    elif isinstance(scores_raw, list):
                        for s in scores_raw:
                            if not isinstance(s, dict):
                                continue
                            score_data = s.get("score", {})
                            participant = score_data.get("participant", "")
                            goals = score_data.get("goals")
                            if s.get("description") == "CURRENT" or not home_score:
                                if participant == "home":
                                    home_score = goals
                                elif participant == "away":
                                    away_score = goals
                    # 获取对手的 SM ID
                    opp_sm_id = None
                    if home_p.get("id") == ref_sm_id:
                        my_score, opp_score = home_score, away_score
                        opp_name = away_p.get("name", "")
                        is_home = True
                        opp_sm_id = away_p.get("id")
                    else:
                        my_score, opp_score = away_score, home_score
                        opp_name = home_p.get("name", "")
                        is_home = False
                        opp_sm_id = home_p.get("id")

                    if my_score is not None and opp_score is not None:
                        if my_score > opp_score: result = "W"
                        elif my_score == opp_score: result = "D"
                        else: result = "L"
                        form_chars.append(result)
                    else:
                        result = "?"
                        form_chars.append("?")

                    recent.append({
                        "opponent": opp_name,
                        "opponent_sm_id": opp_sm_id,
                        "score": f"{my_score}:{opp_score}" if my_score is not None else "?:?",
                        "result": result,
                        "date": str(m.get("starting_at", ""))[:10],
                        "is_home": is_home,
                    })

                form_str = "".join(form_chars) if form_chars else None

                # 更新或创建stats记录
                exist = await db.execute(
                    select(TeamSeasonStats).where(TeamSeasonStats.team_id == tid).order_by(TeamSeasonStats.id.desc()).limit(1)
                )
                stats = exist.scalar_one_or_none()
                if not stats:
                    stats = TeamSeasonStats(team_id=tid, season="latest")
                    db.add(stats)
                    await db.flush()
                if form_str:
                    stats.form = form_str
                if recent:
                    stats.recent_matches = recent

                print(f"    form={form_str} recent={len(recent)}场")

            await db.commit()

    await sm.close()
    print("\n完成!")

asyncio.run(main())
