"""
同步 周五002 (博德闪耀 vs 利勒斯特罗姆) 的 H2H 交锋数据
"""
import asyncio, json
from datetime import datetime
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Team, HeadToHead
from app.collector.sportmonks.client import SportMonksClient

# SM stat code mapping (from pipeline H2H_STAT_CODES)
STAT_CODES = {
    "shots-total": "shots_total",
    "shots-on-target": "shots_on_target",
    "ball-possession": "possession",
    "shots-off-target": "shots_off_target",
    "dangerous-attacks": "dangerous_attacks",
    "corners": "corners",
    "fouls": "fouls",
    "yellowcards": "yellow_cards",
}

async def main():
    sm = SportMonksClient()
    
    # SM team IDs: 博德闪耀=1668, 利勒斯特罗姆=2510
    sm_home, sm_away = 1668, 2510
    db_home, db_away = 170, 1590
    
    print(f"Fetching H2H: SM {sm_home} vs {sm_away}...")
    h2h_data = await sm.get_head_to_head(sm_home, sm_away)
    
    if not isinstance(h2h_data, list) or len(h2h_data) == 0:
        print(f"No H2H data returned from SM API (type={type(h2h_data)})")
        await sm.close()
        return
    
    print(f"Got {len(h2h_data)} H2H fixtures from SM")
    
    async with async_session() as db:
        new_count = 0
        update_count = 0
        
        for h in h2h_data[:6]:
            fixture_id = h.get("id")
            match_date_str = h.get("starting_at", "")
            try:
                match_date = datetime.strptime(match_date_str[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                match_date = datetime.utcnow()
            
            # Resolve participants
            participants = h.get("participants", [])
            if len(participants) < 2:
                continue
            
            local_team_sm = None
            for p in participants:
                if isinstance(p, dict):
                    meta = p.get("meta") or {}
                    if meta.get("location") == "home":
                        local_team_sm = p.get("id")
                        break
            if not local_team_sm and participants:
                local_team_sm = participants[0].get("id")
            
            # Scores
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
            
            # Direction: sm_home=1668 is 博德闪耀
            if local_team_sm == sm_home:
                ht_id, at_id = db_home, db_away
            else:
                ht_id, at_id = db_away, db_home
            
            # Check if already exists by fixture_id
            dup = await db.execute(
                select(HeadToHead).where(HeadToHead.sportmonks_fixture_id == fixture_id)
            )
            existing = dup.scalar_one_or_none()
            
            home_stats = {}
            away_stats = {}
            
            # Fetch match stats
            if fixture_id:
                try:
                    fx_data = await sm.get_fixture_by_id(fixture_id, includes="statistics.type")
                    fx_stats = fx_data.get("statistics", []) if isinstance(fx_data, dict) else []
                    if isinstance(fx_stats, list):
                        for s in fx_stats:
                            if not isinstance(s, dict):
                                continue
                            tid = s.get("participant_id")
                            type_obj = s.get("type") or {}
                            code = type_obj.get("code", "") if isinstance(type_obj, dict) else ""
                            if code not in STAT_CODES or not tid:
                                continue
                            val = (s.get("data") or {}).get("value") if isinstance(s.get("data"), dict) else s.get("data")
                            if val is None:
                                continue
                            try:
                                v = float(val)
                            except (ValueError, TypeError):
                                continue
                            stat_key = STAT_CODES[code]
                            if tid == local_team_sm:
                                home_stats[stat_key] = v
                            else:
                                away_stats[stat_key] = v
                    print(f"  fixture {fixture_id}: home_stats={len(home_stats)}keys, away_stats={len(away_stats)}keys")
                except Exception as e:
                    print(f"  fixture {fixture_id}: stats fetch failed: {e}")
                
                # Fetch xG from trends
                try:
                    tx_data = await sm.get_fixture_by_id(fixture_id, includes="trends")
                    trends = tx_data.get("trends", []) if isinstance(tx_data, dict) else []
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
                        if xg_vals:
                            print(f"  fixture {fixture_id}: xG {xg_vals}")
                except Exception as e:
                    print(f"  fixture {fixture_id}: xG fetch failed: {e}")
            
            if existing:
                # Update stats if missing
                if existing.home_stats is None and home_stats:
                    existing.home_stats = home_stats
                    existing.away_stats = away_stats or None
                    update_count += 1
                    print(f"  Updated stats for existing record {fixture_id}")
                else:
                    print(f"  Skipping existing record {fixture_id} (already has stats)")
            else:
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
                print(f"  Added new record {fixture_id}: {match_date_str} {home_score}-{away_score}")
        
        await db.commit()
        print(f"\nDone. New: {new_count}, Updated: {update_count}")
    
    await sm.close()

asyncio.run(main())
