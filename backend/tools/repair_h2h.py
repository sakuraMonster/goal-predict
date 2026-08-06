"""修复所有历史 HeadToHead 数据：清空后全量重同步"""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv; load_dotenv()
from datetime import datetime
from app.db.database import async_session
from app.db.models import HeadToHead, Match, Team
from app.collector.sportmonks.client import SportMonksClient
from sqlalchemy import select, delete

H2H_STAT_CODES = {
    "shots-total": "shots", "shots-on-target": "shots_on_target",
    "shots-off-target": "shots_off", "shots-blocked": "shots_blocked",
    "attacks": "attacks", "dangerous-attacks": "dangerous",
    "saves": "saves", "fouls": "fouls",
    "ball-possession": "possession", "corners": "corners",
}

async def main():
    async with async_session() as db:
        # 1. 清空 HeadToHead
        count_result = await db.execute(select(HeadToHead))
        old_count = len(count_result.scalars().all())
        await db.execute(delete(HeadToHead))
        await db.commit()
        print(f"已清空 {old_count} 条历史 H2H 记录")

        # 2. 收集所有有 SM ID 的球队对
        matches_result = await db.execute(
            select(Match.home_team_id, Match.away_team_id).where(
                Match.home_team_id.isnot(None),
                Match.away_team_id.isnot(None),
            )
        )
        pairs = set()
        for t1, t2 in matches_result:
            if t1 and t2:
                pairs.add(tuple(sorted([t1, t2])))

        # 3. 查 Team SM IDs
        all_tids = set()
        for t1, t2 in pairs:
            all_tids.add(t1); all_tids.add(t2)

        team_sm = {}
        tid_chunks = [list(all_tids)[i:i+500] for i in range(0, len(all_tids), 500)]
        for chunk in tid_chunks:
            r = await db.execute(select(Team.id, Team.sportmonks_id).where(Team.id.in_(chunk)))
            for tid, sm_id in r:
                if sm_id:
                    team_sm[tid] = sm_id

        print(f"共 {len(pairs)} 对球队，{len(team_sm)}/{len(all_tids)} 支有 SM ID")

        # 4. 为每对球队拉取 H2H
        client = SportMonksClient()
        new_count = 0
        failed = 0

        print(f"开始拉取 {len(pairs)} 对球队的 H2H...")

        for i, (t1, t2) in enumerate(sorted(pairs)):
            sm1 = team_sm.get(t1)
            sm2 = team_sm.get(t2)
            if not sm1 or not sm2:
                failed += 1
                continue

            if i > 0 and i % 10 == 0:
                print(f"  {i}/{len(pairs)} ... (已成功 {new_count}, 失败 {failed})")

            if (i+1) % 100 == 0:
                print(f"  {i+1}/{len(pairs)} ... (已成功 {new_count})")

            try:
                h2h_data = await client.get_head_to_head(sm1, sm2)
            except Exception as e:
                failed += 1
                if failed <= 5:
                    print(f"  H2H team={sm1} vs {sm2} 拉取失败: {e}")
                continue

            if not isinstance(h2h_data, list) or len(h2h_data) == 0:
                continue

            for h in h2h_data[:6]:  # 最多 6 场
                fixture_id = h.get("id")
                if not fixture_id:
                    continue

                match_date_str = h.get("starting_at", "")
                try:
                    match_date = datetime.strptime(match_date_str[:10], "%Y-%m-%d")
                except:
                    match_date = datetime.utcnow()

                participants = h.get("participants", [])
                if len(participants) < 2:
                    continue

                # 按 meta.location 识别主队
                local_team_sm = None
                for p in participants:
                    if isinstance(p, dict):
                        meta = p.get("meta") or {}
                        if meta.get("location") == "home":
                            local_team_sm = p.get("id")
                            break
                if not local_team_sm and participants:
                    local_team_sm = participants[0].get("id")

                # 解析比分
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

                # 确定 DB 方向
                if local_team_sm == sm1:
                    ht_id, at_id = t1, t2
                else:
                    ht_id, at_id = t2, t1

                # stats
                home_stats, away_stats = {}, {}
                try:
                    fx_data = await client.get_fixture_by_id(fixture_id, includes="statistics.type")
                    fx_stats = fx_data.get("statistics", [])
                    if isinstance(fx_stats, list):
                        for s in fx_stats:
                            if not isinstance(s, dict):
                                continue
                            tid = s.get("participant_id")
                            type_obj = s.get("type") or {}
                            code = type_obj.get("code", "") if isinstance(type_obj, dict) else ""
                            if code not in H2H_STAT_CODES or not tid:
                                continue
                            val = (s.get("data") or {}).get("value")
                            if val is None:
                                continue
                            try:
                                v = float(val)
                            except (ValueError, TypeError):
                                continue
                            stat_key = H2H_STAT_CODES[code]
                            if tid == local_team_sm:
                                home_stats[stat_key] = v
                            else:
                                away_stats[stat_key] = v
                except:
                    pass

                # xG
                try:
                    tx_data = await client.get_fixture_by_id(fixture_id, includes="trends")
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
                except:
                    pass

                # 去重检查
                dup = await db.execute(
                    select(HeadToHead).where(HeadToHead.sportmonks_fixture_id == fixture_id)
                )
                if dup.scalar_one_or_none():
                    continue

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
                new_count += 1

            if (i+1) % 500 == 0:
                await db.commit()

        await db.commit()
        print(f"\n完成! 新增 {new_count} 条 H2H 记录，失败 {failed} 对，清空 {old_count} 条旧记录")

asyncio.run(main())
