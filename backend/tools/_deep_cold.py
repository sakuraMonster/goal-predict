"""深度分析001/003的冷门信号来源"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Match, Team, TeamSeasonStats, HeadToHead, OddsSnapshot
from app.predictor.features import FeatureEngineer

IDS = [15463, 15465]  # 001, 003

async def analyze_match(db, mid):
    # Match info
    mr = await db.execute(select(Match).where(Match.id == mid))
    m = mr.scalar_one_or_none()
    if not m: return

    # Team info
    hr = await db.execute(select(Team).where(Team.id == m.home_team_id))
    ar = await db.execute(select(Team).where(Team.id == m.away_team_id))
    ht = hr.scalar_one_or_none()
    at = ar.scalar_one_or_none()

    print(f"\n{'='*80}")
    print(f"  {m.match_num}  {ht.name_zh if ht else '?'} vs {at.name_zh if at else '?'}")
    if m.home_score is not None:
        print(f"  实际比分: {m.home_score}:{m.away_score}")
    print(f"{'='*80}")

    # 1. 赛季数据深度对比
    hs = await db.execute(select(TeamSeasonStats).where(
        TeamSeasonStats.team_id == m.home_team_id
    ).order_by(TeamSeasonStats.season.desc()).limit(1))
    away_s = await db.execute(select(TeamSeasonStats).where(
        TeamSeasonStats.team_id == m.away_team_id
    ).order_by(TeamSeasonStats.season.desc()).limit(1))
    h_stats = hs.scalar_one_or_none()
    a_stats = away_s.scalar_one_or_none()

    if h_stats:
        print(f"\n  主队赛季数据 ({h_stats.season}, 联赛ID={h_stats.league_id}):")
        print(f"    场次={h_stats.played} 胜={h_stats.wins} 平={h_stats.draws} 负={h_stats.losses}")
        print(f"    进球={h_stats.goals_for} 失球={h_stats.goals_against}")
        print(f"    主场胜={h_stats.home_wins}/{h_stats.home_wins+h_stats.home_draws+h_stats.home_losses}")
        print(f"    客场胜={h_stats.away_wins}/{h_stats.away_wins+h_stats.away_draws+h_stats.away_losses}")
        print(f"    场均控球={h_stats.avg_possession}%")
        xg_val = getattr(h_stats, 'avg_xg', None)
        if xg_val is not None:
            xga_val = getattr(h_stats, 'avg_xga', 0)
            print(f"    xG={xg_val:.3f} xGA={xga_val:.3f}")
    if a_stats:
        print(f"\n  客队赛季数据 ({a_stats.season}, 联赛ID={a_stats.league_id}):")
        print(f"    场次={a_stats.played} 胜={a_stats.wins} 平={a_stats.draws} 负={a_stats.losses}")
        print(f"    进球={a_stats.goals_for} 失球={a_stats.goals_against}")
        print(f"    客场胜={a_stats.away_wins}/{a_stats.away_wins+a_stats.away_draws+a_stats.away_losses}")
        print(f"    场均控球={a_stats.avg_possession}%")
        xg_val_a = getattr(a_stats, 'avg_xg', None)
        if xg_val_a is not None:
            xga_val_a = getattr(a_stats, 'avg_xga', 0)
            print(f"    xG={xg_val_a:.3f} xGA={xga_val_a:.3f}")

    # 2. H2H 历史
    h2hr = await db.execute(select(HeadToHead).where(
        ((HeadToHead.home_team_id == m.home_team_id) & (HeadToHead.away_team_id == m.away_team_id)) |
        ((HeadToHead.home_team_id == m.away_team_id) & (HeadToHead.away_team_id == m.home_team_id))
    ).order_by(HeadToHead.match_date.desc()).limit(5))
    h2h_records = list(h2hr.scalars().all())

    print(f"\n  最近H2H交锋 ({len(h2h_records)}场):")
    for hh in h2h_records[:5]:
        hs_name = (await db.execute(select(Team.name_zh).where(Team.id == hh.home_team_id))).scalar() or "?"
        as_name = (await db.execute(select(Team.name_zh).where(Team.id == hh.away_team_id))).scalar() or "?"
        hs_data = json.loads(hh.home_stats) if isinstance(hh.home_stats, str) else (hh.home_stats or {})
        aw_data = json.loads(hh.away_stats) if isinstance(hh.away_stats, str) else (hh.away_stats or {})
        xg_h = hs_data.get("xG", "?") if isinstance(hs_data, dict) else "?"
        xg_a = aw_data.get("xG", "?") if isinstance(aw_data, dict) else "?"
        pos_h = hs_data.get("possession", "?") if isinstance(hs_data, dict) else "?"
        print(f"    {hh.match_date}: {hs_name} {hh.home_score}:{hh.away_score} {as_name}"
              f"  xG={xg_h}:{xg_a} 控球={pos_h}%")

    # 3. 赔率变动时间线
    odds_r = await db.execute(
        select(OddsSnapshot).where(OddsSnapshot.match_id == mid)
        .order_by(OddsSnapshot.snapshot_time.asc())
    )
    all_odds = list(odds_r.scalars().all())

    if all_odds:
        # 按博彩公司分组
        by_bm = {}
        for o in all_odds:
            bm = o.bookmaker or "unknown"
            by_bm.setdefault(bm, []).append(o)

        times = sorted(set(o.snapshot_time for o in all_odds))

        print(f"\n  赔率变动时间线 ({len(by_bm)}家博彩公司, {len(times)}个时间点):")
        if len(times) >= 2:
            first_t = times[0]
            last_t = times[-1]
            # 计算各时间点平均赔率
            for t in [first_t, last_t]:
                odds_at_t = [o for o in all_odds if o.snapshot_time == t]
                if odds_at_t:
                    avg_h = sum(o.home_win for o in odds_at_t if o.home_win) / max(1, len([o for o in odds_at_t if o.home_win]))
                    avg_d = sum(o.draw for o in odds_at_t if o.draw) / max(1, len([o for o in odds_at_t if o.draw]))
                    avg_a = sum(o.away_win for o in odds_at_t if o.away_win) / max(1, len([o for o in odds_at_t if o.away_win]))
                    print(f"    {t}: 均赔 {avg_h:.3f} / {avg_d:.3f} / {avg_a:.3f}  ({len(odds_at_t)}家)")

            # 变动方向
            first_odds = [o for o in all_odds if o.snapshot_time == first_t]
            last_odds = [o for o in all_odds if o.snapshot_time == last_t]
            f_avg_h = sum(o.home_win for o in first_odds if o.home_win) / max(1, len([o for o in first_odds if o.home_win]))
            l_avg_h = sum(o.home_win for o in last_odds if o.home_win) / max(1, len([o for o in last_odds if o.home_win]))
            f_avg_a = sum(o.away_win for o in first_odds if o.away_win) / max(1, len([o for o in first_odds if o.away_win]))
            l_avg_a = sum(o.away_win for o in last_odds if o.away_win) / max(1, len([o for o in last_odds if o.away_win]))
            print(f"    变动: 主 {l_avg_h-f_avg_h:+.3f} ({(l_avg_h-f_avg_h)/f_avg_h*100:+.1f}%)  客 {l_avg_a-f_avg_a:+.3f} ({(l_avg_a-f_avg_a)/f_avg_a*100:+.1f}%)")

            # 各家离散度
            if last_odds:
                last_h = [o.home_win for o in last_odds if o.home_win]
                last_a = [o.away_win for o in last_odds if o.away_win]
                if len(last_h) >= 2:
                    import statistics
                    print(f"    最新离散: 主std={statistics.stdev(last_h):.4f} 客std={statistics.stdev(last_a):.4f}")

    # 4. 特征分析
    eng = FeatureEngineer(db)
    f_df = await eng.extract_features(mid)
    if not f_df.empty:
        f = f_df.iloc[0]

        print(f"\n  关键冷门信号特征:")
        print(f"    season_match_same_league   = {f.get('season_match_same_league', '?'):.0f} (0=非同赛事)")
        print(f"    odds_dispersity            = {f.get('odds_dispersity', '?'):.4f} (分歧度)")
        print(f"    odds_std_home              = {f.get('odds_std_home', '?'):.4f} (主赔标准差)")
        print(f"    bookmaker_intent           = {f.get('bookmaker_intent', '?'):+.2f}")
        print(f"    fundamental_odds_divergence = {f.get('fundamental_odds_divergence', '?'):.4f}")
        print(f"    odds_market_home_prob      = {f.get('odds_market_home_prob', '?'):.4f}")
        print(f"    odds_market_draw_prob      = {f.get('odds_market_draw_prob', '?'):.4f}")
        print(f"    odds_market_away_prob      = {f.get('odds_market_away_prob', '?'):.4f}")

        # 战力对比
        print(f"\n  战力对比:")
        print(f"    主: Win%={f.get('home_win_rate',0):.3f} PPG={f.get('home_points_per_game',0):.2f} Form={f.get('home_form_pts_6',0):.2f} xG={f.get('home_xG',0):.3f}")
        print(f"    客: Win%={f.get('away_win_rate',0):.3f} PPG={f.get('away_points_per_game',0):.2f} Form={f.get('away_form_pts_6',0):.2f} xG={f.get('away_xG',0):.3f}")
        print(f"    差值: WinDiff={f.get('win_rate_diff',0):.3f} PPGDiff={f.get('points_per_game_diff',0):.3f} xGDiff={f.get('xG_diff',0):.3f}")
        print(f"    主场胜率: {f.get('home_home_win_rate',0):.3f}  客场胜率: {f.get('away_away_win_rate',0):.3f}")
        print(f"    休息天: 主{f.get('home_rest_days',0):.1f}  客{f.get('away_rest_days',0):.1f}  差{f.get('rest_days_diff',0):.1f}")

    # 5. 结论
    print(f"\n  ═══ 冷门信号诊断 ═══")
    actual_h = m.home_score or 0
    actual_a = m.away_score or 0
    if actual_h > actual_a: result = "主胜"
    elif actual_h == actual_a: result = "平局"
    else: result = "客胜"

    print(f"  实际结果: {result} ({actual_h}:{actual_a})")

async def main():
    async with async_session() as db:
        for mid in IDS:
            await analyze_match(db, mid)

asyncio.run(main())
