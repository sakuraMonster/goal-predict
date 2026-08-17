"""
OU盘口基准线 & 回落信号改造 开关体系

三级开关，逐级启用：
  Phase 1: 全部 OFF，代码部署后观察新特征输出 1-2 天
  Phase 2: backtest 验证通过后逐级打开
    Day 1: ou_multi_line_storage = True   （多线入库）
    Day 2: ou_new_feature_algorithm = True（新特征计算）
    Day 3: ou_new_model_thresholds = True （模型新阈值）

回退：任意阶段关闭对应开关即可恢复旧逻辑，无需回滚代码。
"""

# ── Phase 3-Day1: 多线OU全部入库 ──
OU_MULTI_LINE_STORAGE = True

# ── Phase 3-Day2: 新特征计算（加权众数 + 双维度回落 + 联赛感知） ──
OU_NEW_FEATURE_ALGORITHM = True

# ── Phase 3-Day3: 模型层使用新特征 + 新阈值 ──
OU_NEW_MODEL_THRESHOLDS = True

# ── Model C v2: 开盘锚定 + 稳健信号（2026-08-11） ──
# goal_line_market 锚定开盘线（第一个 over+under 齐全时刻的共识线），消除最新共识线
# 随快照批次累积横跳（15578 案例）；goal_line_shift 改为"开盘线 vs 近窗口平滑共识线"。
# 注：低源降级组件经实证为净损失（×0.5 丢 2 场命中），OU_LOW_SOURCE_SIGNAL_SCALE 默认 1.0。
# 关闭 → 完全回退到"最新批次共识 + 单批位移"旧逻辑。
OU_OPENING_ANCHOR = True
