# SPF 预测系统改进执行计划

> 诊断日期：2026-07-31 | 数据基础：7月30日 6 场比赛复盘  
> 模型版本：20260730-2154 | 训练数据截止：2026-05-31

---

## 一、诊断摘要

### 1.1 核心指标

| 维度 | T=2.5（旧） | T=1.0（新） | 提升 |
|------|------------|------------|------|
| max 概率均值 | 40.4% | 50.8% | +10.4pp |
| max > 50% 场次 | 0/6 | 3/6 | - |
| max > 45% 场次 | 1/6 | 4/6 | - |
| 预测方向变化 | - | 0/6 | 方向不变 |

### 1.2 根因定位

1. **温度缩放 T=2.5 过度平滑** → 概率区分度损失 ~60%，已通过改为 T=1.0 修复
2. **46/153 特征为跨场常数** → xG、伤病、休息天数、排名、欧战、SM 预测等信号全部失效
3. **训练数据断层 2 个月** → 模型对 7 月新赛季比赛存在分布漂移
4. **球队数据缺失时填 0** → 主客队特征不对称，人为放大概率偏差
5. **70 个低重要性特征被赔率覆盖** → 近期状态、H2H 等特征有数据但模型不学

### 1.3 常数特征清单（46 维）

| 类别 | 特征 | 根因 |
|------|------|------|
| xG（5维） | `home_xG, away_xG, xG_diff, home_xGA, away_xGA` | `TeamSeasonStats` 中 xG 字段全空 |
| 联赛归一（4维） | `home_xg_norm, away_xg_norm, home_possession_tendency, away_possession_tendency, style_clash_possession` | 依赖缺失的 xG 和控球数据 |
| 排名（5维） | `*_league_position, *_rank_percentile, rank_percentile_diff` | `TeamSeasonStats.league_position` 为空，fallback 到固定值 |
| 欧战（7维） | `home/away_uefa_*` | 所有球队无 UEFA 比赛记录 |
| 阵容（5维） | `*_injuries, *_rest_days, rest_days_diff` | 伤停表为空 + rest_days 硬编码为 7.0 |
| SM 预测（5维） | `sm_*_prob` | SportMonks API 无返回，退化为均匀分布 |
| 大小球盘口（5维） | `goal_line_market, over/under_odds_*` | 大小球赔率数据缺失 |
| 分歧/共识（4维） | `*_divergence_trend, *_line_shift` | 无多博彩公司数据 |
| 其他（6维） | `season_stage, is_weekend, has_h2h, odds_time_depth` 等 | 硬编码或固定值 |

---

## 二、执行阶段

### 阶段一：代码修复（预计 1 天）

#### 2.1 关闭温度缩放 ✓ 已完成

- 文件：`app/predictor/models/model_a.py` 第 10 行
- 改动：`CALIBRATION_TEMPERATURE = 2.5` → `1.0`
- 效果：max 概率均值 40.4% → 50.8%

#### 2.2 修复休息天数硬编码 ✓ 已完成

- 文件：`app/predictor/features.py` 第 72-77 行
- 改动：新增 `_get_rest_days` 方法查询 `matches` 表上一场 `kickoff_time`，计算间隔天数
- 无上一场比赛时 fallback 到 7 天
- 效果：3 维特征恢复有实际差异的值（4-350天），常数特征从 46 降到 43

---

### 阶段二：数据断层修复（预计 3-5 天）

#### 3.1 球队数据缺失用联赛均值 fallback ✓ 已完成

- 文件：`app/predictor/features.py` `_extract_team_strength` 方法
- 改动：新增 `baseline` 参数，无 `TeamSeasonStats` 时用联赛均值填充（如 `league_home_win_rate=0.45`），替代填 0.0
- 影响：消除主客队数据不对称的人为偏差
- 注意：昨日 6 场实际数据都完整，fallback 未触发；但对未来数据缺失的比赛有保护作用

#### 3.2 补齐 xG 数据 ✓ 已完成

- 方案：全局赛季 xG 无意义，改用 H2H 交锋记录 xG 填充
- 文件：`app/predictor/features.py`
- 改动：新增 `_fill_xg_from_h2h` 方法，在特征提取完成后将 `h2h_avg_home_xg` / `h2h_avg_away_xg` 回填到 `home_xG / away_xG / home_xGA / away_xGA / xG_diff`
- 效果：5 维 xG 特征 + 4 维归一化特征恢复区分度

#### 3.3 排查 SM 预测 API ✓ 已完成

- 文件：`app/predictor/pipeline.py` + `app/predictor/features.py`
- 改动：
  - 增加 SM API 调用错误日志 + `sm_client.close()` 资源释放
  - SM 预测不可用时，使用市场赔率隐含概率（`odds_market_*_prob`）作为 fallback，替代均匀分布 `1/3`
- 效果：SM 特征从常数变为有信息量的市场赔率信号

#### 3.4 排名特征修复 ✓ 已完成

- 问题：`league_position` 字段全部为空，排名分位恒为 0.5
- 方案：从 `points_per_game` 推算排名分位（PPG/联赛均值比 → sigmoid 映射）
- 文件：`app/predictor/features.py` `_extract_rank_features`
- 效果：5 维排名特征恢复区分度

#### 3.5 实现伤停数据采集

- 数据源：SportMonks `fixtures/{id}/inclusions` 或 `squads/{team}/injuries`
- 表结构：`injuries` 表已有（`team_id, player_name, status, start_date, end_date`）
- 采集逻辑：筛选 `status == "out"` 的条目计数
- 影响：2 维特征恢复

---

### 阶段三：特征工程优化（预计 3-5 天）

#### 4.1 特征消融实验

- 目的：验证近期状态、H2H 等特征的增量价值
- 方法：
  1. 仅保留赔率特征 → 训练 → 测试准确率 A
  2. 赔率 + 基础战力 → 准确率 B
  3. 赔率 + 基础战力 + 近期状态 → 准确率 C
  4. 赔率 + 基础战力 + H2H → 准确率 D
  5. 全特征 → 准确率 E
- 决策标准：若两组差异 < 0.5%，低价值特征可降级或移除

#### 4.2 赛季初期特征增强 ✓ 已完成

- 新增特征 `season_progress_ratio`：已赛场次/赛季总场次，反映磨合程度
- 文件：`app/predictor/features.py` `_extract_contextual`
- 贝叶斯收缩自适应：已赛场次 < 10 时加大 `prior_strength`（从固定 5.0 变为 5+max(0,10-games_played)），赛季初期更依赖先验
- 文件：`app/predictor/features.py` `_extract_recent_form`

#### 4.3 对阵强度特征 ✓ 已完成

- 方案：新增 `match_intensity`（几何平均，区分强强对话 vs 弱弱保级）和 `strength_asymmetry`（归一化差距，区分势均力敌 vs 一边倒）
- 文件：`app/predictor/features.py` `_extract_team_strength`
- 非破坏性改动：保留原有对称特征，仅增加 2 维派生特征

---

### 阶段四：长期架构（按需推进）

#### 5.1 概率校准系统化

- 训练后自动计算 ECE（Expected Calibration Error）
- 通过验证集可靠性曲线搜索最优 T
- 建立校准质量监控

#### 5.2 特征填充率监控

- 每次预测记录各特征类别的填充率
- 填充率 < 50% 的比赛降级处理或标记为数据不足
- 建立趋势看板

#### 5.3 冷门修正机制评估

- 统计冷门标记场次的命中率 vs 非冷门场次
- 评估 `55% 模型 + 45% 市场` 的混合比例是否需要调整

---

### 阶段五：重训练（在所有代码修复完成后执行）

#### R1 重训练模型

- 脚本：`tools/full_retrain.py`（推荐，含联赛修正 + StandardScaler）
- 当前训练数据截止 2026-05-31，届时已包含最新已完成比赛
- 执行后替换 `models/model_a_wl.pkl`、`models/model_a_hcp.pkl`、`models/model_b.pkl`
- 前提：阶段一～二的代码修复已完成 + 数据已更新
- 验证：训练完成后跑一轮预测，对比报告页概率分布与命中率

---

## 三、执行优先级矩阵

```
                    高收益                      低收益
            ┌─────────────────────┬─────────────────────┐
   低成本   │ 阶段一：T=1.0 ✓      │ 阶段一：rest_days    │
            │ 阶段二：球队fallback  │ 阶段三：赛季特征      │
            ├─────────────────────┼─────────────────────┤
   高成本   │ 阶段二：xG 补齐      │ 阶段三：特征消融      │
            │ 阶段二：SM 排查      │ 阶段四：监控系统      │
            │ 阶段二：伤停采集     │ 阶段四：校准系统      │
            │ 阶段五：重训练       │                     │
            └─────────────────────┴─────────────────────┘
```

## 四、执行顺序

```
阶段一（代码修复）
  ├── 2.1 关闭温度缩放 ✓
  └── 2.2 修复 rest_days ✓

阶段二（数据修复）
  ├── 3.1 球队 fallback ✓
  ├── 3.2 补齐 xG（H2H 交锋填充）✓
  ├── 3.3 排查 SM API（日志 + 市场 fallback）✓
  ├── 3.4 排名特征修复 ✓
  └── 3.5 伤停采集

  阶段三（优化）
  ├── 4.1 特征消融实验（重训练后执行）
  ├── 4.2 赛季特征 ✓
  └── 4.3 对阵强度 ✓

阶段四（长期）
  ├── 5.1 校准系统
  ├── 5.2 监控
  └── 5.3 冷门评估

阶段五（重训练）← 所有修复完成后最后执行
  └── R1 重训练模型
      └── 验证：跑预测 → 检查报告页概率分布与命中率
```

---

## 五、验证标准

每阶段完成后通过以下指标验证效果：

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| SPF 命中率（日） | 16.7% (1/6) | > 30% |
| 特征填充率均值 | 74% | > 85% |
| 常数特征数 | 46/153 | < 20/153 |
