"""回填 HeadToHead 缺失的比赛统计数据（xG/射门/控球/威胁进攻）"""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv; load_dotenv()

from app.db.database import async_session
from app.db.models import HeadToHead
from app.collector.sportmonks.client import SportMonksClient
from sqlalchemy import select

H2H_STAT_CODES = {
    "shots-total": "shots", "shots-on-target": "shots_on_target",
    "shots-off-target": "shots_off", "shots-blocked": "shots_blocked",
    "attacks": "attacks", "dangerous-attacks": "dangerous",
    "saves": "saves", "fouls": "fouls",
    "ball-possession": "possession", "corners": "corners",
}

BATCH_SIZE = 50
COMMIT_EVERY = 100

async def main():
    async with async_session() as db:
        # 查询有 SM fixture_id 但缺少 home_stats 的记录
        result = await db.execute(
            select(HeadToHead).where(
                HeadToHead.sportmonks_fixture_id.isnot(None),
                HeadToHead.home_stats.is_(None)
            ).limit(1000)  # 扩大到1000条，分批回填
        )
        records = list(result.scalars().all())
        total = len(records)
        print(f"需要回填 {total} 条 H2H 记录")
        
        if total == 0:
            print("没有需要回填的记录")
            return
        
        client = SportMonksClient()
        updated = 0
        stats_fail = 0
        xg_fail = 0
        skipped = 0
        
        for i, h2h in enumerate(records):
            fixture_id = h2h.sportmonks_fixture_id
            if not fixture_id:
                skipped += 1
                continue
            
            if (i+1) % BATCH_SIZE == 0:
                print(f"  {i+1}/{total} (已更新 {updated}, stats失败 {stats_fail}, xg失败 {xg_fail})")
            
            home_stats, away_stats = {}, {}
            has_data = False
            
            # V4.1: 合并调用 participants + statistics.type + trends
            try:
                fx_data = await client.get_fixture_by_id(
                    fixture_id, includes="participants;statistics.type;trends"
                )
                # 解析 participants → pid→side
                participants = fx_data.get("participants", [])
                pid_side = {}
                for p in participants:
                    pid = p.get("id") if isinstance(p, dict) else None
                    if pid:
                        side = (p.get("meta") or {}).get("location", "home")
                        pid_side[pid] = side
                # statistics
                fx_stats = fx_data.get("statistics", [])
                if isinstance(fx_stats, list):
                    for s in fx_stats:
                        if not isinstance(s, dict): continue
                        tid = s.get("participant_id")
                        type_obj = s.get("type") or {}
                        code = type_obj.get("code", "") if isinstance(type_obj, dict) else ""
                        if code not in H2H_STAT_CODES or not tid: continue
                        val = (s.get("data") or {}).get("value")
                        if val is None: continue
                        v = float(val)
                        target = home_stats if pid_side.get(tid, "home") == "home" else away_stats
                        target[H2H_STAT_CODES[code]] = v
                    if home_stats or away_stats: has_data = True
                # xG from trends
                trends = fx_data.get("trends", [])
                if isinstance(trends, list):
                    xg_by_pid = {}
                    for t in trends:
                        if t.get("type_id") == 117:
                            pid = t.get("participant_id")
                            minute = t.get("minute", 0)
                            val = t.get("value", 0)
                            if pid and val:
                                cur = xg_by_pid.get(pid)
                                if not cur or minute > cur[0]:
                                    xg_by_pid[pid] = (minute, val)
                    for pid, (_, val) in xg_by_pid.items():
                        target = home_stats if pid_side.get(pid, "home") == "home" else away_stats
                        target["xG"] = round(val / 100, 2)
                        has_data = True
            except Exception as e:
                stats_fail += 1
                if stats_fail <= 3:
                    print(f"  [api] fixture={fixture_id}: {type(e).__name__}: {e}")
            
            if has_data:
                h2h.home_stats = home_stats or None
                h2h.away_stats = away_stats or None
                updated += 1
            else:
                skipped += 1
            
            if (i+1) % COMMIT_EVERY == 0:
                await db.commit()
        
        await db.commit()
        print(f"\n完成! 回填 {updated} 条, 跳过 {skipped} 条, stats失败 {stats_fail}, xg失败 {xg_fail}")
        
        # 统计结果
        r2 = await db.execute(select(HeadToHead).where(HeadToHead.home_stats.isnot(None)))
        has_now = len(r2.scalars().all())
        r3 = await db.execute(select(HeadToHead))
        total_now = len(r3.scalars().all())
        print(f"H2H 有比赛统计: {has_now}/{total_now} ({has_now/total_now*100:.1f}%)")

asyncio.run(main())
