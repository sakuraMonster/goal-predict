"""修复周五003赔率主客方向 + 重新同步"""
import asyncio
from datetime import datetime
from sqlalchemy import select, delete
from app.db.database import async_session
from app.db.models import Match, OddsSnapshot
from app.collector.pipeline import SyncPipeline

async def main():
    # Step 1: Fix is_swapped and delete old odds
    async with async_session() as db:
        m = await db.execute(select(Match).where(Match.id == 15471))
        match = m.scalar_one()
        match.is_swapped = False
        r = await db.execute(delete(OddsSnapshot).where(OddsSnapshot.match_id == 15471))
        print(f"Fixed is_swapped=False, deleted {r.rowcount} old odds records")
        await db.commit()
    
    # Step 2: Re-sync odds (via pipeline for this specific match)
    pipeline = SyncPipeline()
    
    def _safe_float(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        try:
            s = str(v).replace(" ", "").split(",")[0]
            return float(s)
        except (ValueError, TypeError):
            return None

    def _norm_side_label(raw):
        s = raw.strip().lower()
        if s in ("1", "home"):
            return "home"
        if s in ("2", "away"):
            return "away"
        return None

    def _norm_1x2_label(raw):
        s = raw.strip().lower()
        if s in ("1", "home"):
            return "home"
        if s in ("x", "draw"):
            return "draw"
        if s in ("2", "away"):
            return "away"
        return None

    async with async_session() as db:
        odds_list = await pipeline.sm.get_odds_pre_match(19609655)
        print(f"Fetched {len(odds_list)} odds from SM")
        
        # Group by bookmaker
        bookmaker_groups = {}
        bookmaker_names = {}
        for o in odds_list:
            bm_id = o.get("bookmaker_id", 0)
            bookmaker_groups.setdefault(bm_id, []).append(o)
            if bm_id not in bookmaker_names:
                bm_obj = o.get("bookmaker", {})
                bm_api_name = bm_obj.get("name", "") if isinstance(bm_obj, dict) else ""
                bookmaker_names[bm_id] = pipeline.BOOKMAKER_NAMES.get(bm_id) or bm_api_name or str(bm_id)
        
        snapshot_time = datetime.utcnow()
        total = 0
        
        for bm_id, odds_items in list(bookmaker_groups.items())[:3]:
            spf = {}
            hcp_by_line = {}
            
            for o in odds_items:
                mid = o.get("market_id")
                label_raw = o.get("label", "")
                value = o.get("value")
                
                if mid == pipeline.MARKET_1X2:
                    key = _norm_1x2_label(label_raw)
                    if key:
                        spf[key] = value
                elif mid == pipeline.MARKET_HANDICAP:
                    line = _safe_float(o.get("handicap"))
                    if line is None:
                        continue
                    key = _norm_side_label(label_raw)
                    if key is None:
                        continue
                    hcp_by_line.setdefault(line, {})[key] = value
            
            home_win = _safe_float(spf.get("home"))
            draw = _safe_float(spf.get("draw"))
            away_win = _safe_float(spf.get("away"))
            
            bm_name = bookmaker_names.get(bm_id, str(bm_id))
            print(f"\n{bm_name}: home_win={home_win}, draw={draw}, away_win={away_win}")
            
            if hcp_by_line:
                for line, hcp in hcp_by_line.items():
                    hh = _safe_float(hcp.get("home"))
                    ha = _safe_float(hcp.get("away"))
                    if hh is None or ha is None:
                        continue
                    
                    db.add(OddsSnapshot(
                        match_id=15471,
                        snapshot_time=snapshot_time,
                        bookmaker=bm_name,
                        home_win=home_win,
                        draw=draw,
                        away_win=away_win,
                        handicap_line=line,
                        handicap_home=hh,
                        handicap_away=ha,
                    ))
                    total += 1
            else:
                # No handicap data, still store 1X2
                db.add(OddsSnapshot(
                    match_id=15471,
                    snapshot_time=snapshot_time,
                    bookmaker=bm_name,
                    home_win=home_win,
                    draw=draw,
                    away_win=away_win,
                ))
                total += 1
        
        await db.commit()
        print(f"\nInserted {total} odds snapshots (is_swapped=False, no swap)")
    
    await pipeline.sm.close()

asyncio.run(main())
