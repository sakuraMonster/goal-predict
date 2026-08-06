"""杯赛专项分析：按实际类型拆分组"""
import asyncio, sys, os, math
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import joinedload
from app.db.database import engine
from app.db.models import Prediction, Match
from app.predictor.pipeline import PredictionPipeline

# 韩K球队关键词
K_LEAGUE_TEAMS = {"全北", "蔚山", "首尔", "江原", "浦项", "金泉", "大田", "光州",
                   "济州", "仁川", "水原", "大邱", "富川", "安养"}

def classify_cup(home: str, away: str) -> str:
    """根据队名判断杯赛实际类型"""
    is_k_home = any(kw in home for kw in K_LEAGUE_TEAMS)
    is_k_away = any(kw in away for kw in K_LEAGUE_TEAMS)
    if is_k_home and is_k_away:
        return "韩K联"
    # 巴西常见队名
    br_teams = {"桑托斯", "米拉索尔", "格雷米奥", "巴西国际", "科林蒂安", "巴拉纳", "维多利亚",
                "弗拉门戈", "帕尔梅拉斯", "圣保罗", "弗鲁米嫩", "巴伊亚", "里莫"}
    is_br_home = any(t in home for t in br_teams)
    is_br_away = any(t in away for t in br_teams)
    if is_br_home and is_br_away:
        return "巴西杯赛"
    return "欧战杯赛"

async def main():
    sf = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 7, 28, 12, 0, 0)
    end = datetime(2026, 8, 4, 12, 0, 0)

    async with sf() as db:
        result = await db.execute(
            select(Prediction)
            .options(joinedload(Prediction.match).joinedload(Match.home_team),
                     joinedload(Prediction.match).joinedload(Match.away_team),
                     joinedload(Prediction.match).joinedload(Match.league))
            .where(and_(Prediction.kickoff_time >= start, Prediction.kickoff_time < end,
                        Prediction.actual_home_score.isnot(None)))
            .order_by(Prediction.kickoff_time)
        )
        preds = list(result.unique().scalars().all())

        known = {'美职联', '芬超', '瑞典超', '挪超', '巴甲', '欧冠'}
        pipeline = PredictionPipeline(db)

        # 按组收集
        groups = defaultdict(list)

        for p in preds:
            m = p.match
            if not m: continue
            lg = p.league.name_zh if p.league else None
            if lg and lg in known:
                continue  # 只看未知联赛

            mid = m.id
            home = (m.home_team.name_zh if m.home_team else m.home_team_name) or "?"
            away = (m.away_team.name_zh if m.away_team else m.away_team_name) or "?"
            actual_h = p.actual_home_score or 0
            actual_a = p.actual_away_score or 0
            actual_total = actual_h + actual_a

            try:
                pred = await pipeline.predict(mid)
            except Exception as e:
                print(f"  MID={mid} FAIL: {e}")
                continue

            raw_lam = pred.get("raw_lambda", 0)
            adj_lam = pred.get("expected_goals", 0)
            cold = pred.get("is_cold_match", False)

            eg_clean = round(adj_lam, 10)
            frac = eg_clean - math.floor(eg_clean)
            if frac < 0.10: eff = math.floor(eg_clean)
            elif frac > 0.90: eff = math.ceil(eg_clean)
            else: eff = adj_lam
            dists = sorted([(abs(eff - k), k) for k in range(5)])
            top2 = sorted([dists[0][1], dists[1][1]])
            act_capped = min(actual_total, 4)
            snap_hit = act_capped in top2

            cat = classify_cup(home, away)
            groups[cat].append({
                "home": home, "away": away, "actual_total": actual_total,
                "score": f"{actual_h}:{actual_a}",
                "raw_lam": raw_lam, "adj_lam": adj_lam,
                "top2": f"{top2[0]}/{top2[1]}", "snap_hit": snap_hit,
                "cold": cold,
            })

        # ── 按组输出 ──
        total_all = 0
        hits_all = 0
        for cat in ["韩K联", "巴西杯赛", "欧战杯赛"]:
            g = groups[cat]
            if not g: continue
            n = len(g)
            h = sum(1 for x in g if x["snap_hit"])
            overs = sum(1 for x in g if x["actual_total"] > 2.5)
            avg_lam = sum(x["adj_lam"] for x in g) / n
            avg_raw = sum(x["raw_lam"] for x in g) / n
            total_all += n
            hits_all += h

            print(f"\n{'='*80}")
            print(f"  {cat} ({n}场)  SNAP={h}/{n} ({h/n*100:.0f}%)  大球={overs}  均λ={avg_lam:.2f}  均raw_λ={avg_raw:.2f}")
            print(f"{'='*80}")

            for x in g:
                s = "V" if x["snap_hit"] else "X"
                err = x["adj_lam"] - x["actual_total"]
                print(f"  {s} {x['home']:12s} vs {x['away']:12s} {x['score']:>5} T={x['actual_total']} | "
                      f"raw_λ={x['raw_lam']:.2f}→adj={x['adj_lam']:.2f} top2={x['top2']} err={err:+.1f} "
                      f"{'冷' if x['cold'] else ''}")

        print(f"\n{'='*80}")
        print(f"  杯赛汇总: SNAP={hits_all}/{total_all} ({hits_all/total_all*100:.0f}%)")

asyncio.run(main())
