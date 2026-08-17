"""单场调试：分析新特征 vs 旧特征在恶化场次上的差异，定位 lambda 剧变根因"""
import sys, os, asyncio, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.db.database import async_session
from app.db.models import Match, Prediction
from app.predictor.features_b import FeatureEngineerB
from app.predictor.models.model_c import ModelC
from app.predictor.features_base import BaseDataFetcher
from app import ou_flags

# 恶化场次
MATCH_IDS = [15522, 15567, 15533, 15529]  # 15522=桑纳菲尤尔vs奥斯陆 挪超


async def main():
    print(f"OU flags: storage={ou_flags.OU_MULTI_LINE_STORAGE} feature={ou_flags.OU_NEW_FEATURE_ALGORITHM} model={ou_flags.OU_NEW_MODEL_THRESHOLDS}")
    async with async_session() as db:
        for mid in MATCH_IDS:
            r = await db.execute(
                select(Match).options(joinedload(Match.league)).where(Match.id == mid)
            )
            m = r.unique().scalar_one_or_none()
            if not m:
                continue
            lg = m.league.name_zh if m.league else "?"
            eng = FeatureEngineerB(db)
            df = await eng.extract_features(mid)
            f = df.iloc[0].to_dict()

            pred = (await db.execute(select(Prediction).where(Prediction.match_id == mid))).scalar_one_or_none()
            actual = pred.actual_total_goals if pred else None

            # 关键 OU 特征对比
            print(f"\n{'='*70}")
            print(f"[{mid}] {m.home_team_name} vs {m.away_team_name} ({lg}) 实际总进球={actual}")
            print(f"  新: GL_market={f.get('goal_line_market')} shift={f.get('goal_line_shift')} drift={f.get('odds_drift_over_mean')} cons={f.get('odds_drift_consensus')} vol={f.get('goal_line_volatility')} drop={f.get('goal_line_drop_from_peak')}")
            print(f"  旧: GL_market_old={f.get('goal_line_market_old')} drop_old={f.get('goal_line_drop_from_peak_old')} max_old={f.get('goal_line_max_old')} min_old={f.get('goal_line_min_old')}")
            print(f"  其他: home_gf={f.get('home_goals_avg')} away_gf={f.get('away_goals_avg')} h6={f.get('home_gf_avg_6')} a6={f.get('away_gf_avg_6')} fund={f.get('fundamental_vs_market_divergence')}")

            # 新旧路径预测
            rc = ModelC().predict(f, lg)
            d = rc["detail"]
            print(f"  新预测: lam={rc['expected_goals']} mkt_lam={d['lambda_market']} fund_lam={d['lambda_fundamental']} mw={d['market_weight']} induce={d['induce_score']} drop_adj={d['drop_adj']} GL={d['goal_line']} rule={d['league_rule_applied']}")

            # 旧路径：手动模拟（旧特征值）
            f_old = dict(f)
            f_old["goal_line_market"] = f_old.get("goal_line_market_old")
            f_old["goal_line_drop_from_peak"] = f_old.get("goal_line_drop_from_peak_old")
            f_old["goal_line_max"] = f_old.get("goal_line_max_old")
            from app.predictor.models import model_c as mc_mod
            orig = mc_mod.ou_flags.OU_NEW_MODEL_THRESHOLDS
            mc_mod.ou_flags.OU_NEW_MODEL_THRESHOLDS = False
            rc_old = ModelC().predict(f_old, lg)
            mc_mod.ou_flags.OU_NEW_MODEL_THRESHOLDS = orig
            d2 = rc_old["detail"]
            print(f"  旧路径(旧特征+旧阈值): lam={rc_old['expected_goals']} mkt_lam={d2['lambda_market']} fund_lam={d2['lambda_fundamental']} mw={d2['market_weight']} induce={d2['induce_score']} drop_adj={d2['drop_adj']} GL={d2['goal_line']}")
            if pred:
                print(f"  DB旧值: expected_goals_c={pred.expected_goals_c} snap_top2_c={pred.snap_top2_c}")


asyncio.run(main())
