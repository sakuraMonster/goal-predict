"""为002手动拉取H2H"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv; load_dotenv()
from datetime import datetime
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import HeadToHead, Team
from app.collector.sportmonks.client import SportMonksClient

H2H_STAT_CODES = {
    "shots-total": "shots", "shots-on-target": "shots_on_target",
    "shots-off-target": "shots_off", "attacks": "attacks",
    "dangerous-attacks": "dangerous", "ball-possession": "possession",
    "corners": "corners", "fouls": "fouls", "saves": "saves",
}

async def main():
    async with async_session() as db:
        ht = await db.get(Team, 170)  # 博德闪耀
        at = await db.get(Team, 1608)  # 利勒斯特罗姆
        sm1, sm2 = ht.sportmonks_id, at.sportmonks_id
        print(f'博德闪耀 sm_id={sm1}, 利勒斯特罗姆 sm_id={sm2}')

        client = SportMonksClient()
        try:
            h2h_data = await client.get_head_to_head(sm1, sm2)
            print(f'H2H returned {len(h2h_data) if isinstance(h2h_data,list) else 0} fixtures')
            
            count = 0
            for h in (h2h_data if isinstance(h2h_data,list) else [])[:6]:
                fid = h.get("id")
                date_str = h.get("starting_at", "")[:10]
                print(f'\n  fixture={fid} date={date_str}')
                
                # 检查是否已存在
                exist = await db.execute(select(HeadToHead).where(HeadToHead.sportmonks_fixture_id == fid))
                if exist.scalar_one_or_none():
                    print(f'    已存在，跳过')
                    continue
                
                # 识别主客
                participants = h.get("participants", [])
                local_team_sm = None
                for p in participants:
                    if (p.get("meta") or {}).get("location") == "home":
                        local_team_sm = p.get("id")
                        break
                
                ht_id = 170 if local_team_sm == sm1 else 1608
                at_id = 1608 if ht_id == 170 else 170
                
                # 比分
                scores = h.get("scores", []) or []
                home_score = away_score = None
                for s in scores:
                    if s.get("description") == "CURRENT":
                        g = (s.get("score") or {}).get("goals")
                        if s.get("participant_id") == local_team_sm:
                            home_score = g
                        else:
                            away_score = g
                
                # 拉取stats
                home_stats, away_stats = {}, {}
                try:
                    fx = await client.get_fixture_by_id(fid, includes="participants;statistics.type")
                    pid_side = {}
                    for p in fx.get("participants", []):
                        pid = p.get("id")
                        if pid:
                            pid_side[pid] = (p.get("meta") or {}).get("location", "home")
                    
                    for s in fx.get("statistics", []):
                        tid = s.get("participant_id")
                        code = (s.get("type") or {}).get("code", "")
                        if code not in H2H_STAT_CODES: continue
                        val = (s.get("data") or {}).get("value")
                        if val is None: continue
                        target = home_stats if pid_side.get(tid)=="home" else away_stats
                        target[H2H_STAT_CODES[code]] = float(val)
                    
                    # 代理xG
                    if home_stats:
                        h_s = home_stats.get("shots",0) or 0; h_st = home_stats.get("shots_on_target",0) or 0
                        a_s = away_stats.get("shots",0) or 0; a_st = away_stats.get("shots_on_target",0) or 0
                        home_stats["xG"] = round(0.07*h_s + 0.10*h_st, 2)
                        away_stats["xG"] = round(0.07*a_s + 0.10*a_st, 2)
                except Exception as e:
                    print(f'    stats拉取失败: {e}')
                
                date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.utcnow()
                db.add(HeadToHead(
                    home_team_id=ht_id, away_team_id=at_id,
                    match_date=date, competition=h.get("league",{}).get("name",""),
                    home_score=home_score, away_score=away_score,
                    sportmonks_fixture_id=fid,
                    home_stats=home_stats or None, away_stats=away_stats or None,
                ))
                count += 1
                has = "xG" if home_stats else "no stats"
                print(f'    OK ht={ht_id} at={at_id} {home_score}:{away_score} {has}')
            
            await db.commit()
            print(f'\n新增 {count} 条H2H')
        finally:
            await client.close()

asyncio.run(main())
