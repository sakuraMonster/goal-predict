"""验证 _compute_ou_features 新旧双输出逻辑（单元测试，不依赖 DB）"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
from collections import namedtuple
from app.predictor.features_base import BaseDataFetcher
from app.ou_flags import OU_NEW_FEATURE_ALGORITHM

# 构造一个假的 BaseDataFetcher（避免 __init__ 依赖 DB）
class FakeFetcher(BaseDataFetcher):
    def __init__(self):
        pass

f = FakeFetcher()

# 模拟 OddsSnapshot 对象
Snap = namedtuple("Snap", ["snapshot_time", "bookmaker", "goal_line", "over_odds", "under_odds", "is_opening"])

t1 = datetime(2026, 8, 9, 10, 0)
t2 = datetime(2026, 8, 9, 12, 0)
t3 = datetime(2026, 8, 9, 14, 0)

# 场景: 韩K 联赛, 2家博彩公司, 初盘2.5, 临场降到2.25
all_odds_list = [
    # 初盘 t1: 两家都开 2.5
    Snap(t1, "bm1", 2.5, 1.85, 1.95, True),
    Snap(t1, "bm2", 2.5, 1.87, 1.93, True),
    # 中段 t2: bm1 降到 2.25, bm2 保持 2.5
    Snap(t2, "bm1", 2.25, 1.90, 1.90, False),
    Snap(t2, "bm2", 2.5, 1.86, 1.94, False),
    # 即时 t3: 两家都降到 2.25
    Snap(t3, "bm1", 2.25, 1.80, 2.00, False),
    Snap(t3, "bm2", 2.25, 1.82, 1.98, False),
]

times = [t1, t2, t3]
by_bm = {}
latest = [s for s in all_odds_list if s.snapshot_time == t3]

# 设置联赛名
f._current_league_name = "韩K"

# 用韩K范围 [1.75, 3.0]
ou_feats = f._compute_ou_features(all_odds_list, times, by_bm, latest, "韩K")

print("=== 新特征 (OU_NEW_FEATURE_ALGORITHM=%s) ===" % OU_NEW_FEATURE_ALGORITHM)
for k in ["goal_line_market", "goal_line_market_old", "goal_line_drop_from_peak",
          "goal_line_drop_from_peak_old", "odds_drift_over_mean",
          "odds_drift_consensus", "goal_line_shift", "goal_line_volatility",
          "over_odds_current", "over_odds_decline_rate", "goal_line_max_old"]:
    print(f"  {k} = {ou_feats.get(k)}")

# 断言
assert ou_feats["goal_line_market"] == 2.25, f"新基准线应为2.25, got {ou_feats['goal_line_market']}"
assert ou_feats["goal_line_shift"] == 0.25, f"整线位移应为0.25, got {ou_feats['goal_line_shift']}"
assert ou_feats["goal_line_drop_from_peak"] == 0.25, "goal_line_drop_from_peak 应=整线位移"
assert ou_feats["odds_drift_over_mean"] > 0, "同线水位漂移应为正(Over赔率下跌=看小)"
assert ou_feats["odds_drift_consensus"] >= 0.5, "漂移一致性应>=0.5"
assert ou_feats["over_odds_current"] == 1.81, f"即时Over水位应1.81, got {ou_feats['over_odds_current']}"
print("\n✅ 所有断言通过")

# 验证旧对照逻辑
print("\n旧对照: goal_line_drop_from_peak_old =", ou_feats["goal_line_drop_from_peak_old"])
print("旧对照: goal_line_max_old =", ou_feats["goal_line_max_old"])
