# MarketFlow（盘口结构 + 风格标签）预测链路 — 设计规格文档

> 日期：2026-08-20 | 状态：设计阶段完成，待进入实施计划 | 范围：后端新增独立链路与数据结构，不改前端界面

---

## 一、目标与非目标

### 1.1 目标

- 新增一条独立预测链路（MarketFlow），严格按“盘口结构 + 风格标签”的人工推理流程固化为可运行的后端模块。
- 输出粒度固定为：
  - **进球数**：最优 1 个 + 次优 1 个
  - **比分**：最优 1 个 + 次优 1 个
- 全流程可回测：保存输入快照与推理过程 trace，保证结果可复现、可复盘。
- 与现有预测体系强隔离（数据库表与 API 独立），避免影响现有线上口径与 UI。

### 1.2 非目标（本期不做）

- 不改前端任何页面与交互。
- 不实现“赔率自动采集/同步”（用户后续提供历史数据做回测；采集工作另起任务）。
- 不做风格自动推断（风格标签来自数据库配置）。
- 不做去水/隐含概率归一化（逻辑完全基于赔率结构与相对关系）。

---

## 二、输入与输出

### 2.1 输入

**风格标签（数据库配置）**

- 主队风格：防守型 / 均衡 / 大开大合
- 客队风格：防守型 / 均衡 / 大开大合

**赔率快照（以“玩法-投注项-赔率”的结构存储）**

- 胜平负（HAD）：主胜/平/客胜
- 让球胜平负（HHAD）：让球线（竞彩 goalLineValue）+ 让胜/让平/让负
- 总进球（TTG）：0/1/2/3/4/5/6/7+ 档位赔率（实际以输入为准）
- 比分（CRS）：常见比分投注项赔率（例如 1-0/2-1/1-1 等；以输入为准）

### 2.2 输出

- 进球数最优：best_total_goals（int）
- 进球数次优：second_total_goals（int）
- 比分最优：best_score（string，如 "2-1"）
- 比分次优：second_score（string）
- trace_json：推理过程快照（用于回测与复盘）

---

## 三、数据模型（与现有系统隔离）

### 3.1 Team 增加风格标签

- 在 `teams` 表新增字段：
  - `style_tag`：String(20)，取值限定为：防守型 / 均衡 / 大开大合

说明：
- 风格标签是“输入的一部分”。预测写入时需把主客风格写入 MarketFlow 结果表作为快照，避免未来调整风格导致历史回测不可复现。

### 3.2 新增赔率快照表（独立于 odds_snapshots）

表名建议：`jczq_play_odds_snapshots`

用途：
- 存储 MarketFlow 所需的“竞彩足球玩法赔率”快照，与现有 SportMonks/欧赔/大小球结构解耦。
- 支持后续由用户导入历史数据、或未来补齐采集器写入。

字段建议（最小可用集合）：

```sql
jczq_play_odds_snapshots (
  id                serial primary key,
  match_id           int not null references matches(id),
  snapshot_time      timestamp not null,
  source             varchar(50) not null, -- manual_import / jczq / ...

  -- HAD
  had_home           float,
  had_draw           float,
  had_away           float,

  -- HHAD
  hhad_line          float,
  hhad_home          float,
  hhad_draw          float,
  hhad_away          float,

  -- TTG/CRS：玩法-投注项-赔率映射
  ttg_odds_json      jsonb,
  crs_odds_json      jsonb
)
```

### 3.3 新增预测结果表（独立于 predictions）

表名建议：`market_flow_predictions`

用途：
- 存储 MarketFlow 的输出与推理 trace。
- 以 `match_id` 唯一，避免同一场重复写多条（如需多版本，使用 model_version 区分或改为多条并加 unique 约束组合）。

字段建议：

```sql
market_flow_predictions (
  id                 serial primary key,
  match_id            int not null unique references matches(id),
  odds_snapshot_id    int not null references jczq_play_odds_snapshots(id),
  model_version       varchar(50) not null,
  created_at          timestamp not null,

  home_style_tag      varchar(20) not null,
  away_style_tag      varchar(20) not null,

  best_total_goals    int,
  second_total_goals  int,
  best_score          varchar(10),
  second_score        varchar(10),

  trace_json          jsonb
)
```

trace_json 建议包含：
- 输入风格标签与来源
- 比分盘排序、断层判断依据、候选池
- 候选池映射的总进球集合与频次
- 风格剪枝后的候选优先级
- TTG 校验过程与最终进球数选择依据
- 比分最终二选一的 tie-break 依据（HAD/HHAD）

---

## 四、固化推理流程（不去水版本）

### 4.1 风格合成（对局风格）

将主客风格合成为对局风格，用于“剪枝/重排”，不用于凭空生成候选：

- 若任一方为防守型，且另一方不为明显大开大合：对局风格偏“防守”
- 若任一方为大开大合，且另一方不为强防守：对局风格偏“大开大合”
- 其它：对局风格“均衡”

### 4.2 比分盘候选池（断层优先，否则 TopK）

- 将 CRS 赔率从低到高排序
- 若出现明显断层（低赔率簇与后续赔率簇存在量级抬升）：取断层前的所有比分作为候选池
- 若无断层：候选池取 TopK（默认 K=6）
- 若候选池数量过少：向后补足到不少于 4 个

### 4.3 候选池反推总进球集合

- 将候选比分映射为总进球 g（x-y → g=x+y）
- 得到候选总进球集合 G，并记录每个 g 的出现频次（用于和 TTG/风格互证）

### 4.4 风格剪枝（只剪枝/重排）

按对局风格对 G 与比分候选池做重排/降权：

- 防守型：优先 1、2；其次 3；显著压制 4+
- 均衡：优先 2、3；其次 1/4
- 大开大合：优先 3、4；其次 2/5；显著压制 0/1

### 4.5 TTG 校验并缩到 2 个总进球（最优/次优）

- 在“候选总进球集合 G ∩ 风格优先序列”范围内，按 TTG 赔率由低到高选择：
  - best_total_goals
  - second_total_goals
- 若出现同赔率或 TTG 数据缺失：
  - 优先用候选池频次更高的 g
  - 再用风格优先级打破平局

### 4.6 比分缩到 2 个（最优/次优）

**最优比分 best_score**

- 在 CRS 中筛出所有总进球=best_total_goals 的比分，取赔率最低者为 best_score

**次优比分 second_score（两条路径择优）**

- 路径 A：仍在 best_total_goals 下，取次低赔率比分
- 路径 B：在 second_total_goals 下取最低赔率比分
- 优先规则：当路径 A 与 B 分歧明显时，优先符合胜平负方向（HAD）的那条

**同赔率 tie-break（关键固化规则）**

当出现同赔率对称比分（典型：2-1 与 1-2）时：

1) 用 HAD 方向打破：主胜倾向选主胜比分，客胜倾向选客胜比分，平局倾向选平局比分  
2) 若 HAD 仍不足以区分，用 HHAD 打破：将比分映射为让球胜平负结果（依据 hhad_line），选择更符合 HHAD 低赔率方向的比分

---

## 五、后端模块与 API 设计

### 5.1 模块边界

- 在 `backend/app/predictor/models/` 下新增 `market_flow.py`（或单独文件夹 `market_flow/`），实现：
  - 数据结构校验（CRS/TTG 格式、必要字段）
  - 纯函数式推理核心（便于回测）
  - trace 生成

### 5.2 API（管理端/回测端）

新增独立路由（不影响现有预测口径），建议在 `backend/app/api/` 下新增 `market_flow.py` 并在 main.py 注册：

- 导入赔率快照（用于回测/手工数据注入）
  - `POST /api/market-flow/odds-snapshots`
  - 入参：match_id、snapshot_time、source、HAD/HHAD/TTG/CRS
  - 出参：odds_snapshot_id

- 运行 MarketFlow（单场）
  - `POST /api/market-flow/predict/{match_id}`
  - 入参：odds_snapshot_id（或自动取最新），model_version（可选）
  - 出参：best/second goals & score + trace_json

- 批量回测（按比赛时间范围/比赛日）
  - `POST /api/market-flow/backtest`
  - 入参：时间范围、source 过滤、是否覆盖已有结果等
  - 出参：命中统计（进球数/比分）与分层统计（按风格/联赛/赔率档位）

---

## 六、回测一致性与可复现原则

- 所有预测必须绑定到一个具体的 odds_snapshot_id
- market_flow_predictions 保存 home_style_tag/away_style_tag 快照
- trace_json 必须足够让人复盘到“哪一步剪掉了哪个候选、为什么最后选这两个”

