"""修复周日001(15475)和周日003(15477)的 is_swapped + 重同步赔率"""
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select, delete
from app.db.database import async_session
from app.db.models import Match, OddsSnapshot
from app.collector.sportmonks.client import SportMonksClient

def _safe_float(v):
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    try:
        s = str(v).replace(" ", "").split(",")[0]
        return float(s)
    except (ValueError, TypeError):
        return None

def _norm_side_label(raw):
    s = raw.strip().lower()
    if s in ("1", "home"): return "home"
    if s in ("2", "away"): return "away"
    return None

def _norm_1x2_label(raw):
    s = raw.strip().lower()
    if s in ("1", "home"): return "home"
    if s in ("x", "draw"): return "draw"
    if s in ("2", "away"): return "away"
    return None

BOOKMAKER_NAMES = {1: "10Bet", 2: "bet365", 5: "威廉希尔", 9: "Betfair", 11: "立博",
    14: "Bwin", 16: "Marathonbet", 20: "Pinnacle", 23: "Pinnacle", 29: "澳门",
    34: "Sbo", 35: "1xbet", 38: "皇冠", 44: "易胜博", 47: "12BET"}

async def fix_match(match_id, sm_fx_id):
    sm = SportMonksClient()
    
    # Step 1: Fix is_swapped and delete old odds
    async with async_session() as db:
        m = await db.execute(select(Match).where(Match.id == match_id))
        match = m.scalar_one()
        match.is_swapped = False
        r = await db.execute(delete(OddsSnapshot).where(OddsSnapshot.match_id == match_id))
        print(f"[{match.match_num}] is_swapped=False, deleted {r.rowcount} old odds")
        await db.commit()
    
    # Step 2: Re-sync odds
    odds_list = await sm.get_odds_pre_match(sm_fx_id)
    
    bookmaker_groups = {}
    bookmaker_names = {}
    for o in odds_list:
        bm_id = o.get("bookmaker_id", 0)
        bookmaker_groups.setdefault(bm_id, []).append(o)
        if bm_id not in bookmaker_names:
            bm_obj = o.get("bookmaker", {})
            bm_api_name = bm_obj.get("name", "") if isinstance(bm_obj, dict) else ""
            bookmaker_names[bm_id] = BOOKMAKER_NAMES.get(bm_id) or bm_api_name or str(bm_id)
    
    async with async_session() as db:
        snapshot_time = datetime.now(timezone.utc).replace(tzinfo=None)
        total = 0
        
        for bm_id, odds_items in list(bookmaker_groups.items())[:3]:
            spf = {}
            hcp_by_line = {}
            for o in odds_items:
                mid = o.get("market_id")
                label_raw = o.get("label", "")
                value = o.get("value")
                if mid == 1:  # 1X2
                    key = _norm_1x2_label(label_raw)
                    if key: spf[key] = value
                elif mid == 6:  # Handicap
                    line = _safe_float(o.get("handicap"))
                    if line is None: continue
                    key = _norm_side_label(label_raw)
                    if key is None: continue
                    hcp_by_line.setdefault(line, {})[key] = value
            
            home_win = _safe_float(spf.get("home"))
            draw = _safe_float(spf.get("draw"))
            away_win = _safe_float(spf.get("away"))
            bm_name = bookmaker_names.get(bm_id, str(bm_id))
            print(f"  {bm_name}: home={home_win}, draw={draw}, away={away_win}")
            
            if hcp_by_line:
                for line, hcp in hcp_by_line.items():
                    hh = _safe_float(hcp.get("home"))
                    ha = _safe_float(hcp.get("away"))
                    if hh is None or ha is None: continue
                    db.add(OddsSnapshot(match_id=match_id, snapshot_time=snapshot_time,
                        bookmaker=bm_name, home_win=home_win, draw=draw, away_win=away_win,
                        handicap_line=line, handicap_home=hh, handicap_away=ha))
                    total += 1
            else:
                db.add(OddsSnapshot(match_id=match_id, snapshot_time=snapshot_time,
                    bookmaker=bm_name, home_win=home_win, draw=draw, away_win=away_win))
                total += 1
        
        await db.commit()
        print(f"  -> {total} snapshots inserted\n")
    
    await sm.close()

async def main():
    await fix_match(15475, 19648109)  # 周日001
    await fix_match(15477, 19648108)  # 周日003

asyncio.run(main())
