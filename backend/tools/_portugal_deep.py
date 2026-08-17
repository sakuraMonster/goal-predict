"""葡超深度分析：逐场比赛完整画像 + 根因定位
1) 每场比赛：盘口/博彩公司/实际/模型分量
2) 两队球队统计：_get_team_stats 实际选中的记录（season/played/recent_matches）+ 该队全部记录
3) 市场准确性：goal_line vs 实际总进球
4) 特征质量：gf_6/ga_6 缺失情况
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction, TeamSeasonStats, OddsSnapshot, Team
from app.predictor.models.model_c import ModelC
from app.predictor.features_b import FeatureEngineerB
from app.predictor.snap import snap_top2

OUT = os.path.join(os.path.dirname(__file__), "portugal_deep.txt")

TGT_LEAGUE = "葡超"
START = __import__("datetime").datetime(2026, 7, 11, 12, 0, 0)
END = __import__("datetime").datetime(2026, 8, 11, 12, 0, 0)


async def main():
    lines = []
    def p(s=""):
        lines.append(s)

    async with async_session() as db:
        feat = FeatureEngineerB(db)
        model_c = ModelC()

        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.league))
            .where(Prediction.kickoff_time >= START, Prediction.kickoff_time < END)
        )
        preds = result.unique().scalars().all()
        tgts = [(pr.match, pr) for pr in preds
                if pr.match and pr.match.league and pr.match.league.name_zh == TGT_LEAGUE]

        p("=" * 100)
        p(f"【Part 1】{TGT_LEAGUE} 逐场比赛完整画像（{len(tgts)} 场）")
        p("=" * 100)

        for match, pred in tgts:
            p("\n" + "-" * 90)
            kt = match.kickoff_time.strftime("%m-%d %H:%M") if match.kickoff_time else "?"
            ht = match.home_team_name or "?"
            at = match.away_team_name or "?"
            actual = pred.actual_total_goals
            p(f"[{kt}] id={match.id} {ht} vs {at} | 实际={actual}球({pred.actual_score}) | 线上λc={pred.expected_goals_c} snap_c={pred.snap_top2_c}")

            # ── 盘口快照（全部博彩公司） ──
            r = await db.execute(
                select(OddsSnapshot).where(OddsSnapshot.match_id == match.id)
                .order_by(OddsSnapshot.snapshot_time.asc(), OddsSnapshot.bookmaker.asc())
            )
            odds = r.scalars().all()
            p(f"  盘口快照 {len(odds)} 条:")
            seen = set()
            for o in odds:
                key = (o.bookmaker, o.goal_line)
                if key in seen:
                    continue
                seen.add(key)
                p(f"    [{o.bookmaker}] GL={o.goal_line} 大={o.over_odds} 小={o.under_odds} 开={o.is_opening}")

            # ── 模型重跑 ──
            try:
                fdf = await feat.extract_features(match.id)
                if fdf.empty:
                    p("  [SKIP] 特征为空")
                    continue
                feats = fdf.iloc[0].to_dict()
            except Exception as e:
                p(f"  [FAIL] 特征提取失败: {type(e).__name__}: {e}")
                continue

            rc = model_c.predict(feats, TGT_LEAGUE)
            d = rc["detail"]
            p(f"  重跑: λ={rc['expected_goals']} SNAP={snap_top2(rc['expected_goals'])} | GL={d['goal_line']} calib={d['calib']}")
            p(f"    λ_market={d['lambda_market']} λ_fund={d['lambda_fundamental']} div={d['divergence']} "
              f"mkt_w={d['market_weight']} early={d.get('early_season_applied')} "
              f"played={d.get('home_games_played')}/{d.get('away_games_played')}")
            p(f"    主攻={d['home_goals_avg']} 客攻={d['away_goals_avg']} 主近6={d['home_gf_avg_6']} 客近6={d['away_gf_avg_6']}")

            # ── 两队全部赛季统计记录 ──
            for side, tname, team_id in [("主", ht, match.home_team_id), ("客", at, match.away_team_id)]:
                r = await db.execute(
                    select(TeamSeasonStats).where(TeamSeasonStats.team_id == team_id)
                    .order_by(TeamSeasonStats.id.desc())
                )
                recs = r.scalars().all()
                p(f"  [{side}队] {tname} (team_id={team_id}) 全部统计记录:")
                for s in recs:
                    rm = s.recent_matches if isinstance(s.recent_matches, list) else []
                    rm_n = len(rm)
                    rm_first = json.dumps(rm[0], ensure_ascii=False)[:80] if rm else "-"
                    p(f"    season={s.season!r} played={s.played} gf={s.goals_for} ga={s.goals_against} "
                      f"w={s.wins}/d={s.draws}/l={s.losses} rm={rm_n}条 最近1场={rm_first}")
                if not recs:
                    p(f"    (无任何统计记录)")

        # ── Part 2: 市场 vs 实际 ──
        p("\n" + "=" * 100)
        p("【Part 2】盘口 vs 实际：市场对葡超揭幕轮的定价准确性")
        p("=" * 100)
        p(f"{'比赛':<30}{'GL':>6}{'实际':>6}{'偏差':>7}  判定")
        for match, pred in tgts:
            kt = match.kickoff_time.strftime("%m-%d") if match.kickoff_time else "?"
            ht = (match.home_team_name or "?")[:12]
            at = (match.away_team_name or "?")[:12]
            actual = pred.actual_total_goals
            gl = None
            r = await db.execute(
                select(func.avg(OddsSnapshot.goal_line)).where(
                    OddsSnapshot.match_id == match.id, OddsSnapshot.goal_line.isnot(None))
            )
            gl = r.scalar_one()
            if gl and actual is not None:
                diff = actual - gl
                judge = "市场高估" if diff < -0.5 else ("市场低估" if diff > 0.5 else "市场准确")
            else:
                diff = None
                judge = "未结算/无盘口"
            p(f"{kt} {ht}vs{at}:{match.id:<6}{'%s' % gl:>6}{'%s' % actual:>6}{'%s' % (round(diff,1) if diff is not None else '-'):>7}  {judge}")

        # ── Part 3: 命中/未命中特征规律 ──
        p("\n" + "=" * 100)
        p("【Part 3】命中 vs 未命中 规律对比（重跑口径）")
        p("=" * 100)
        hit_rows, miss_rows = [], []
        for match, pred in tgts:
            if pred.actual_total_goals is None:
                continue
            actual = pred.actual_total_goals
            try:
                fdf = await feat.extract_features(match.id)
                if fdf.empty:
                    continue
                feats = fdf.iloc[0].to_dict()
                rc = model_c.predict(feats, TGT_LEAGUE)
                exp = rc["expected_goals"]
                snap = snap_top2(exp)
                hit = min(actual, 4) in snap
            except Exception:
                continue
            d = rc["detail"]
            row = {
                "id": match.id, "hit": hit, "exp": exp, "actual": actual,
                "gl": d["goal_line"], "lam_m": d["lambda_market"], "lam_f": d["lambda_fundamental"],
                "mw": d["market_weight"], "early": d.get("early_season_applied"),
                "hgf6": d["home_gf_avg_6"], "agf6": d["away_gf_avg_6"],
                "hgp": d.get("home_games_played"), "agp": d.get("away_games_played"),
            }
            (hit_rows if hit else miss_rows).append(row)

        p(f"\n命中 {len(hit_rows)} 场 vs 未中 {len(miss_rows)} 场")
        p(f"{'比赛':<8}{'结果':<5}{'λ':>6}{'实际':>6}{'GL':>6}{'λmkt':>7}{'λfund':>7}{'mw':>6}{'early':>6}{'主近6':>7}{'客近6':>7}")
        for r in hit_rows + miss_rows:
            p(f"{r['id']:<8}{'HIT' if r['hit'] else 'MISS':<5}{r['exp']:>6.2f}{r['actual']:>6}{r['gl']:>6.2f}"
              f"{r['lam_m']:>7.2f}{r['lam_f']:>7.2f}{r['mw']:>6.2f}{str(r['early']):>6}"
              f"{r['hgf6']:>7.2f}{r['agf6']:>7.2f}")

        # 均值
        def avg(rows, key):
            vals = [r[key] for r in rows if r[key] is not None]
            return sum(vals) / len(vals) if vals else None
        p("\n指标均值对比:")
        for k, label in [("exp", "λ预测"), ("actual", "实际"), ("gl", "盘口"),
                         ("lam_f", "λ_fund"), ("mw", "市场权重"), ("hgf6", "主近6"), ("agf6", "客近6")]:
            hv, mv = avg(hit_rows, k), avg(miss_rows, k)
            p(f"  {label:<6}: 命中={hv if hv is None else round(hv,3)}  未中={mv if mv is None else round(mv,3)}")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"输出: {OUT}")


asyncio.run(main())
