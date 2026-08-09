"""拉取欧罗巴赛事赔率 + H2H + 球队统计"""
import asyncio
from datetime import datetime, timedelta
from dateutil.parser import parse as dateparse
from sqlalchemy import select, delete
from dotenv import load_dotenv
load_dotenv()

from app.db.database import async_session, engine
from app.db.models import Match, OddsSnapshot, HeadToHead, TeamSeasonStats, Team, League
from app.collector.sportmonks.client import SportMonksClient
from app.collector.pipeline import SyncPipeline

async def main():
    pipeline = SyncPipeline()
    
    # ── 1. 拉取赔率 (针对缺失赔率的 4 场欧罗巴) ──
    print("=== 拉取赔率 ===")
    async with async_session() as db:
        result = await db.execute(
            select(Match).where(Match.id.in_([15515, 15516, 15517, 15518]))
        )
        matches = result.scalars().all()
        
        for m in matches:
            if not m.sportmonks_fixture_id:
                print(f"  {m.id} 无 SM fixture，跳过")
                continue
            
            print(f"  {m.id} ({m.home_team_name} vs {m.away_team_name}) fixture={m.sportmonks_fixture_id}")
            
            # 清除旧赔率（如果有）
            await db.execute(delete(OddsSnapshot).where(OddsSnapshot.match_id == m.id))
            
            try:
                odds_list = await pipeline.sm.get_odds_pre_match(m.sportmonks_fixture_id)
            except Exception as e:
                print(f"    赔率拉取失败: {e}")
                continue
            
            if not odds_list:
                print(f"    无赔率数据")
                continue
            
            # 按 bookmaker 分组
            bookmaker_groups = {}
            for o in odds_list:
                bm_id = o.get("bookmaker_id", 0)
                bookmaker_groups.setdefault(bm_id, []).append(o)
            
            snapshot_time = datetime.utcnow()
            count = 0
            for bm_id, items in list(bookmaker_groups.items())[:3]:  # 前3家
                spf = {}
                hcp_by_line = {}
                ou_data = {}
                
                for o in items:
                    mid = o.get("market_id")
                    label = str(o.get("label", "")).strip().lower()
                    value = o.get("value")
                    
                    if mid == pipeline.MARKET_1X2:
                        if label in ("1", "home"): spf["home"] = value
                        elif label in ("x", "draw"): spf["draw"] = value
                        elif label in ("2", "away"): spf["away"] = value
                    elif mid == pipeline.MARKET_HANDICAP:
                        line = o.get("handicap")
                        if line is not None:
                            try:
                                line = float(str(line).replace(" ", "").split(",")[0])
                            except: continue
                            key = "home" if label in ("1", "home") else "away" if label in ("2", "away") else None
                            if key:
                                hcp_by_line.setdefault(line, {})[key] = value
                    elif mid == pipeline.MARKET_OVER_UNDER:
                        if not ou_data:
                            ou_data[label] = value
                
                def _f(v):
                    """安全转 float"""
                    if v is None:
                        return None
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        return None
                
                for line, hcp in hcp_by_line.items():
                    db.add(OddsSnapshot(
                        match_id=m.id,
                        snapshot_time=snapshot_time,
                        bookmaker=str(bm_id),
                        home_win=_f(spf.get("home")),
                        draw=_f(spf.get("draw")),
                        away_win=_f(spf.get("away")),
                        handicap_home=_f(hcp.get("home")),
                        handicap_line=line,
                        handicap_away=_f(hcp.get("away")),
                        over_odds=_f(ou_data.get("over")),
                        goal_line=2.5,
                        under_odds=_f(ou_data.get("under")),
                    ))
                    count += 1
            
            await db.commit()
            print(f"    写入 {count} 条赔率")
    
    # ── 2. 拉取 H2H ──
    print("\n=== 拉取 H2H ===")
    
    def _parse_date(d):
        if d is None:
            return None
        if isinstance(d, datetime):
            return d
        try:
            return dateparse(str(d))
        except:
            return None
    
    async with async_session() as db:
        # 所有今天+明天的赛事
        result = await db.execute(
            select(Match).where(Match.id.in_([15511, 15512, 15513, 15514, 15515, 15516, 15517, 15518]))
        )
        matches = result.scalars().all()
        
        for m in matches:
            ht = await db.get(Team, m.home_team_id) if m.home_team_id else None
            at = await db.get(Team, m.away_team_id) if m.away_team_id else None
            
            if not ht or not at or not ht.sportmonks_id or not at.sportmonks_id:
                print(f"  {m.id}: 缺少 SM ID (h={ht.sportmonks_id if ht else None}, a={at.sportmonks_id if at else None})")
                continue
            
            # 检查是否已有 H2H
            existing = await db.execute(
                select(HeadToHead).where(
                    HeadToHead.home_team_id == m.home_team_id,
                    HeadToHead.away_team_id == m.away_team_id,
                )
            )
            if existing.scalars().all():
                print(f"  {m.id}: H2H 已存在，跳过")
                continue
            
            print(f"  {m.id} ({ht.name_en} vs {at.name_en}) sm={ht.sportmonks_id} vs {at.sportmonks_id}")
            try:
                h2h_list = await pipeline.sm.get_head_to_head(ht.sportmonks_id, at.sportmonks_id)
            except Exception as e:
                print(f"    H2H 拉取失败: {e}")
                continue
            
            if not h2h_list:
                print(f"    无 H2H 数据")
                continue
            
            count = 0
            for h in h2h_list[:8]:
                scores_raw = h.get("scores", {})
                if isinstance(scores_raw, list):
                    scores = scores_raw[0] if scores_raw else {}
                else:
                    scores = scores_raw
                db.add(HeadToHead(
                    home_team_id=m.home_team_id,
                    away_team_id=m.away_team_id,
                    match_date=_parse_date(h.get("starting_at")),
                    competition=h.get("league", {}).get("name") if isinstance(h.get("league"), dict) else None,
                    home_score=scores.get("localteam_score") if isinstance(scores, dict) else None,
                    away_score=scores.get("visitorteam_score") if isinstance(scores, dict) else None,
                    sportmonks_fixture_id=h.get("id"),
                ))
                count += 1
            await db.commit()
            print(f"    写入 {count} 条 H2H")
        
        # 相反方向也查一遍
        for m in matches:
            ht = await db.get(Team, m.home_team_id) if m.home_team_id else None
            at = await db.get(Team, m.away_team_id) if m.away_team_id else None
            if not ht or not at or not ht.sportmonks_id or not at.sportmonks_id:
                continue
            
            existing = await db.execute(
                select(HeadToHead).where(
                    HeadToHead.home_team_id == m.away_team_id,
                    HeadToHead.away_team_id == m.home_team_id,
                )
            )
            if existing.scalars().all():
                continue
            
            try:
                h2h_list = await pipeline.sm.get_head_to_head(at.sportmonks_id, ht.sportmonks_id)
            except:
                continue
            
            if not h2h_list:
                continue
            
            count = 0
            for h in h2h_list[:8]:
                scores_raw = h.get("scores", {})
                if isinstance(scores_raw, list):
                    scores = scores_raw[0] if scores_raw else {}
                else:
                    scores = scores_raw
                db.add(HeadToHead(
                    home_team_id=m.away_team_id,
                    away_team_id=m.home_team_id,
                    match_date=_parse_date(h.get("starting_at")),
                    competition=h.get("league", {}).get("name") if isinstance(h.get("league"), dict) else None,
                    home_score=scores.get("localteam_score") if isinstance(scores, dict) else None,
                    away_score=scores.get("visitorteam_score") if isinstance(scores, dict) else None,
                    sportmonks_fixture_id=h.get("id"),
                ))
                count += 1
            if count > 0:
                await db.commit()
                print(f"  {m.id} 反向: 写入 {count} 条 H2H")
    
    await pipeline.sm.close()
    print("\n全部完成!")

asyncio.run(main())
