"""强制重新同步所有赛事对的 H2H 数据"""
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.collector.sportmonks.client import SportMonksClient
from app.db.database import async_session
from sqlalchemy import select
from app.db.models import HeadToHead, Match, Team
from datetime import datetime

async def main():
    sm = SportMonksClient()
    async with async_session() as db:
        # 获取所有有 sportmonks_fixture_id 的比赛
        result = await db.execute(
            select(Match).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
            )
        )
        matches = result.scalars().all()
        print(f"共 {len(matches)} 场比赛")

        # 收集唯一球队对
        seen_pairs = set()
        h2h_count = 0
        skip_existing = 0

        for match in matches:
            t1, t2 = match.home_team_id, match.away_team_id
            pair_key = tuple(sorted([t1, t2]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            # 获取 SportMonks ID
            t1_result = await db.execute(select(Team.sportmonks_id).where(Team.id == t1))
            t2_result = await db.execute(select(Team.sportmonks_id).where(Team.id == t2))
            sm_id1 = t1_result.scalar()
            sm_id2 = t2_result.scalar()

            if not sm_id1 or not sm_id2:
                print(f"  跳过: 球队 {t1}/{t2} 无 SportMonks ID (sm1={sm_id1}, sm2={sm_id2})")
                continue

            # 检查是否已有 H2H 记录
            exist_result = await db.execute(
                select(HeadToHead).where(
                    (HeadToHead.home_team_id == t1) & (HeadToHead.away_team_id == t2)
                ).limit(1)
            )
            if exist_result.scalar_one_or_none():
                skip_existing += 1
                continue

            try:
                h2h_data = await sm.get_head_to_head(sm_id1, sm_id2)
            except Exception as e:
                print(f"  H2H API 失败: sm_id1={sm_id1} vs sm_id2={sm_id2}: {e}")
                continue

            if not isinstance(h2h_data, list) or len(h2h_data) == 0:
                print(f"  无交锋记录: sm_id1={sm_id1} vs sm_id2={sm_id2}")
                continue

            pair_h2h = 0
            for h in h2h_data[:6]:
                fixture_id = h.get("id")
                match_date_str = h.get("starting_at", "")
                try:
                    match_date = datetime.strptime(match_date_str[:10], "%Y-%m-%d")
                except (ValueError, TypeError):
                    match_date = datetime.now()

                participants = h.get("participants", [])
                if len(participants) < 2:
                    continue

                local_team_sm = participants[0].get("id") if isinstance(participants[0], dict) else None

                # 比分
                scores_list = h.get("scores", []) or []
                home_score = away_score = None
                for s in scores_list:
                    if not isinstance(s, dict) or s.get("description") != "CURRENT":
                        continue
                    goals = (s.get("score") or {}).get("goals")
                    pid = s.get("participant_id")
                    if pid == local_team_sm:
                        home_score = goals
                    else:
                        away_score = goals

                if local_team_sm == sm_id1:
                    ht_id, at_id = t1, t2
                else:
                    ht_id, at_id = t2, t1
                    home_score, away_score = away_score, home_score

                home_stats = {}
                away_stats = {}

                # 获取详细统计数据
                if fixture_id:
                    try:
                        fx_data = await sm.get_fixture_by_id(fixture_id, includes="statistics.type")
                        fx_stats = fx_data.get("statistics", [])
                        if isinstance(fx_stats, list):
                            for s in fx_stats:
                                if not isinstance(s, dict):
                                    continue
                                tid = s.get("participant_id")
                                type_obj = s.get("type") or {}
                                code = type_obj.get("code", "") if isinstance(type_obj, dict) else ""
                                # 使用与 pipeline 相同的 H2H_STAT_CODES
                                code_map = {
                                    "shots-total": "shots",
                                    "shots-on-target": "shots_on_target",
                                    "shots-off-target": "shots_off",
                                    "shots-blocked": "shots_blocked",
                                    "attacks": "attacks",
                                    "dangerous-attacks": "dangerous",
                                    "saves": "saves",
                                    "fouls": "fouls",
                                    "ball-possession": "possession",
                                    "corners": "corners",
                                }
                                if code not in code_map or not tid:
                                    continue
                                val = (s.get("data") or {}).get("value") if isinstance(s.get("data"), dict) else s.get("data")
                                if val is None:
                                    continue
                                try:
                                    v = float(val)
                                except (ValueError, TypeError):
                                    continue
                                stat_key = code_map[code]
                                if tid == local_team_sm:
                                    home_stats[stat_key] = v
                                else:
                                    away_stats[stat_key] = v
                    except Exception as e:
                        print(f"    统计获取失败 fixture={fixture_id}: {e}")

                    # 获取 xG 数据
                    try:
                        tx_data = await sm.get_fixture_by_id(fixture_id, includes="trends")
                        trends = tx_data.get("trends", [])
                        if isinstance(trends, list):
                            xg_vals = {}
                            for t in trends:
                                if t.get("type_id") == 117:
                                    pid = t.get("participant_id")
                                    minute = t.get("minute", 0)
                                    val = t.get("value", 0)
                                    if pid and val:
                                        cur = xg_vals.get(pid)
                                        if not cur or minute > cur[0]:
                                            xg_vals[pid] = (minute, val)
                            for pid, (_, val) in xg_vals.items():
                                xg = val / 100
                                if pid == local_team_sm:
                                    home_stats["xG"] = round(xg, 2)
                                else:
                                    away_stats["xG"] = round(xg, 2)
                    except Exception:
                        pass

                db.add(HeadToHead(
                    home_team_id=ht_id,
                    away_team_id=at_id,
                    match_date=match_date,
                    competition=h.get("league", {}).get("name", "") if isinstance(h.get("league"), dict) else "",
                    home_score=home_score,
                    away_score=away_score,
                    sportmonks_fixture_id=fixture_id,
                    home_stats=home_stats or None,
                    away_stats=away_stats or None,
                ))
                h2h_count += 1
                pair_h2h += 1

            if pair_h2h > 0:
                print(f"  已同步: sm_id1={sm_id1} vs sm_id2={sm_id2} → {pair_h2h} 条交锋记录")

        await db.commit()
        print(f"\n完成: 新增 {h2h_count} 条 H2H 记录，跳过 {skip_existing} 对已有数据，共处理 {len(seen_pairs)} 对球队")

    await sm.close()

asyncio.run(main())
