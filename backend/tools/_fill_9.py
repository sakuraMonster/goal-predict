"""只回填未来9场H2H的stats数据"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv; load_dotenv()
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Match, HeadToHead
from app.collector.sportmonks.client import SportMonksClient

FUTURE_IDS = [15469,15470,15471,15472,15473,15474,15475,15476,15477]
H2H_STAT_CODES = {
    "shots-total": "shots", "shots-on-target": "shots_on_target",
    "shots-off-target": "shots_off", "attacks": "attacks",
    "dangerous-attacks": "dangerous", "ball-possession": "possession",
    "corners": "corners", "fouls": "fouls", "saves": "saves",
}

async def main():
    async with async_session() as db:
        # 收集所有需要回填的H2H记录
        all_h2h = []
        for mid in FUTURE_IDS:
            m = await db.get(Match, mid)
            if not m: continue
            hr = await db.execute(select(HeadToHead).where(
                ((HeadToHead.home_team_id==m.home_team_id)&(HeadToHead.away_team_id==m.away_team_id))|
                ((HeadToHead.home_team_id==m.away_team_id)&(HeadToHead.away_team_id==m.home_team_id)),
                HeadToHead.sportmonks_fixture_id.isnot(None),
                HeadToHead.home_stats.is_(None)
            ))
            for h in hr.scalars().all():
                all_h2h.append((h, m.match_num, m.home_team_id, m.home_team_name, m.away_team_name))

        print(f"需要回填: {len(all_h2h)} 条 H2H")
        if not all_h2h:
            print("全部已有数据")
            return

        client = SportMonksClient()
        updated = 0
        stats_fail = 0

        for i, (h2h, match_num, home_tid, home_name, away_name) in enumerate(all_h2h):
            fid = h2h.sportmonks_fixture_id
            print(f"  [{i+1}/{len(all_h2h)}] {match_num} fixture={fid} ... ", end="", flush=True)

            home_stats, away_stats = {}, {}
            has_data = False

            # 拉取stats（用participants的meta.location区分主客）
            try:
                fx_data = await client.get_fixture_by_id(fid, includes="participants;statistics.type")
                # 解析主客映射
                pid_side = {}
                for p in fx_data.get("participants", []):
                    pid = p.get("id") if isinstance(p,dict) else None
                    if pid:
                        side = (p.get("meta") or {}).get("location", "home")
                        pid_side[pid] = side
                
                fx_stats = fx_data.get("statistics", [])
                if isinstance(fx_stats, list):
                    for s in fx_stats:
                        if not isinstance(s, dict): continue
                        tid = s.get("participant_id")
                        type_obj = s.get("type") or {}
                        code = type_obj.get("code","") if isinstance(type_obj,dict) else ""
                        if code not in H2H_STAT_CODES or not tid: continue
                        val = (s.get("data") or {}).get("value") if isinstance(s.get("data"),dict) else s.get("data")
                        if val is None: continue
                        try:
                            v = float(val)
                        except (ValueError, TypeError):
                            continue
                        target = home_stats if pid_side.get(tid, "home") == "home" else away_stats
                        target[H2H_STAT_CODES[code]] = v
                    if home_stats: has_data = True
            except Exception as e:
                stats_fail += 1
                print(f"stats error: {e}")
                continue

            # 代理xG
            if home_stats:
                h_shots = home_stats.get("shots", 0) or 0
                h_sot = home_stats.get("shots_on_target", 0) or 0
                a_shots = away_stats.get("shots", 0) or 0
                a_sot = away_stats.get("shots_on_target", 0) or 0
                home_stats["xG"] = round(0.07 * h_shots + 0.10 * h_sot, 2)
                away_stats["xG"] = round(0.07 * a_shots + 0.10 * a_sot, 2)

            if has_data:
                h2h.home_stats = home_stats
                h2h.away_stats = away_stats
                updated += 1
                print(f"OK shots={home_stats.get('shots','?')}/{away_stats.get('shots','?')} xG={home_stats['xG']}/{away_stats['xG']}")
            else:
                print("no stats")

            if (i+1) % 50 == 0:
                await db.commit()

        await db.commit()
        print(f"\n完成! 回填 {updated} 条, 失败 {stats_fail} 条")
        await client.close()

asyncio.run(main())
