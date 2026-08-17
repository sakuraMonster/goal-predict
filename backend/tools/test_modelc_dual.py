"""验证 ModelC 双路径（OU_NEW_MODEL_THRESHOLDS 开/关）"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.ou_flags as flags
from app.predictor.models.model_c import ModelC

m = ModelC()

# 韩K场景: 盘口回落 + 客攻强（旧逻辑触发韩K规则）
features = {
    "goal_line_market": 2.25,
    "home_goals_avg": 1.1,
    "away_goals_avg": 1.3,
    "home_goals_against_avg": 1.0,
    "away_goals_against_avg": 1.2,
    "league_avg_total_goals": 2.4,
    "home_gf_avg_6": 0,
    "away_gf_avg_6": 0,
    "goal_line_drop_from_peak": 0.8,     # 旧量纲: 触发韩K规则 (>0.5)
    "goal_line_volatility": 0.2,
    "odds_dispersity": 0.05,
    "bookmaker_count": 5,
    "odds_consensus_direction": 0,
    "handicap_consensus_direction": 0,
    "fundamental_vs_market_divergence": 0,
    # 新特征（OU_NEW_MODEL_THRESHOLDS=True 时使用）
    "odds_drift_over_mean": 0.10,
    "odds_drift_consensus": 0.8,
    "goal_line_shift": 0.25,
}

# 1. 旧路径
flags.OU_NEW_MODEL_THRESHOLDS = False
r_old = m.predict(features, "韩K")
print("=== 旧路径 (OU_NEW_MODEL_THRESHOLDS=False) ===")
print(f"  expected_goals={r_old['expected_goals']}")
print(f"  induce_score={r_old['detail']['induce_score']}")
print(f"  drop_adj={r_old['detail']['drop_adj']}")
print(f"  league_rule={r_old['detail']['league_rule_applied']}")

# 2. 新路径
flags.OU_NEW_MODEL_THRESHOLDS = True
r_new = m.predict(features, "韩K")
print("\n=== 新路径 (OU_NEW_MODEL_THRESHOLDS=True) ===")
print(f"  expected_goals={r_new['expected_goals']}")
print(f"  induce_score={r_new['detail']['induce_score']}")
print(f"  drop_adj={r_new['detail']['drop_adj']}")
print(f"  league_rule={r_new['detail']['league_rule_applied']}")

print("\n✅ ModelC 双路径测试完成（输出供人工对比，无硬断言）")
