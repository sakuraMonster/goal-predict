"""检查未来9场的特征完整性"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Match, HeadToHead, OddsSnapshot, TeamSeasonStats, Injury

FUTURE_IDS = [15469, 15470, 15471, 15472, 15473, 15474, 15475, 15476, 15477]
NAMES = {
    15469: "周五001 瓦勒伦加 vs 汉坎",
    15470: "周五002 博德闪耀 vs 利勒斯特罗姆",
    15471: "周五003 纽约城 vs 多伦多FC",
    15472: "周六001 江原FC vs 富川FC",
    15473: "周六002 全北现代 vs 首尔FC",
    15474: "周六003 浦项制铁 vs 金泉尚武",
    15475: "周日001 蔚山现代 vs 安养FC",
    15476: "周日002 济州SK vs 仁川联",
    15477: "周日003 大田市民 vs 光州FC",
}

async def main():
    async with async_session() as db:
        for mid in FUTURE_IDS:
            m = await db.get(Match, mid)
            if not m: continue
            
            issues = []
            
            # 1. 赛季数据
            hs = await db.execute(select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == m.home_team_id
            ).order_by(TeamSeasonStats.season.desc()).limit(1))
            away_s = await db.execute(select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == m.away_team_id
            ).order_by(TeamSeasonStats.season.desc()).limit(1))
            h_stats = hs.scalar_one_or_none()
            a_stats = away_s.scalar_one_or_none()
            if not h_stats: issues.append("主队无赛季数据")
            if not a_stats: issues.append("客队无赛季数据")
            
            # 2. H2H
            h2h_cnt = await db.execute(select(func.count(HeadToHead.id)).where(
                ((HeadToHead.home_team_id == m.home_team_id) & (HeadToHead.away_team_id == m.away_team_id)) |
                ((HeadToHead.home_team_id == m.away_team_id) & (HeadToHead.away_team_id == m.home_team_id))
            ))
            h2h_total = h2h_cnt.scalar() or 0
            
            h2h_with_xg = await db.execute(select(func.count(HeadToHead.id)).where(
                ((HeadToHead.home_team_id == m.home_team_id) & (HeadToHead.away_team_id == m.away_team_id)) |
                ((HeadToHead.home_team_id == m.away_team_id) & (HeadToHead.away_team_id == m.home_team_id)),
                HeadToHead.home_stats.isnot(None)
            ))
            h2h_xg_count = h2h_with_xg.scalar() or 0
            
            if h2h_total == 0: issues.append("无H2H记录")
            elif h2h_xg_count == 0: issues.append(f"H2H({h2h_total}场)无xG数据")
            elif h2h_xg_count < h2h_total: issues.append(f"H2H({h2h_total}场)仅{h2h_xg_count}场有xG")
            
            # 3. 赔率
            odds_cnt = await db.execute(select(func.count(OddsSnapshot.id)).where(OddsSnapshot.match_id == mid))
            odds_total = odds_cnt.scalar() or 0
            if odds_total < 10:
                issues.append(f"赔率仅{odds_total}条")
            
            # 4. 伤停
            h_inj = await db.execute(select(func.count(Injury.id)).where(Injury.team_id == m.home_team_id, Injury.status == "out"))
            a_inj = await db.execute(select(func.count(Injury.id)).where(Injury.team_id == m.away_team_id, Injury.status == "out"))
            # 伤停不是关键
            
            # 5. 同联赛检测
            same_league = ""
            if h_stats and a_stats and m.league_id:
                hl = h_stats.league_id
                al = a_stats.league_id
                if hl != m.league_id or al != m.league_id:
                    same_league = " ⚠非同赛事"
            
            status = "✓" if not issues else f"⚠ {len(issues)}项缺失"
            print(f"\n{NAMES.get(mid, 'ID='+str(mid))}  {status}{same_league}")
            if h_stats: print(f"  主: season={h_stats.season} played={h_stats.played} league_id={h_stats.league_id}")
            if a_stats: print(f"  客: season={a_stats.season} played={a_stats.played} league_id={a_stats.league_id}")
            print(f"  H2H: {h2h_total}场 ({h2h_xg_count}场有stats)  赔率: {odds_total}条")
            for issue in issues:
                print(f"    → {issue}")

asyncio.run(main())
