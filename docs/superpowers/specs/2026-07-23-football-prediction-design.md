# 竞彩足球智能化预测系统 — 设计规格文档

> 日期：2026-07-23 | 状态：设计阶段完成，待进入实施计划

---

## 一、系统概述

基于中国体育彩票竞彩足球的智能化预测系统，整合 500.com 竞彩数据、SportMonks v3 API 数据，通过双模型机器学习架构实现胜平负、让球胜平负、进球数、比分四维预测，并提供可视化看板、自动报告输出和复盘统计功能。

---

## 二、技术栈

| 层级 | 选型 |
|------|------|
| 后端框架 | Python 3.12 + FastAPI |
| 前端框架 | React 18 + TypeScript |
| 图表库 | ECharts |
| 样式方案 | Tailwind CSS |
| 数据库 | PostgreSQL（SQLAlchemy ORM） |
| 任务调度 | APScheduler + Redis 分布式锁 |
| 机器学习 | scikit-learn + LightGBM + Poisson Regression |
| 模型管理 | MLflow |
| 报告模板 | Jinja2 |
| 部署方式 | Docker Compose（本地）→ 远程部署（二期） |

---

## 三、数据采集模块

### 3.1 数据源

| 数据源 | 内容 | 获取方式 |
|--------|------|----------|
| trade.500.com/jczq | 每日竞彩赛程、赔率数据 | Playwright 无头浏览器 / XHR API 抓取 |
| liansai.500.com | 联赛球队中文名称列表 | HTTP 请求 + BeautifulSoup |
| api.sportmonks.com/v3 | 球队档案、历史交锋、伤病、赔率、赛季统计 | REST API（API Key 认证） |

### 3.2 联赛策略

动态发现：从 500.com 每日开售赛事中自动识别涉及的联赛，按需从 SportMonks 拉取对应联赛数据。不预定义固定联赛清单。

### 3.3 历史数据深度

拉取最近 3 个完整赛季 + 当前赛季进行中的数据作为模型训练集。

### 3.4 容错机制

- 500.com 请求失败：3 次指数退避重试，失败记日志
- SportMonks 限流：Token Bucket 限速器，429 自动等待
- 单场数据缺失：跳过不阻塞其他赛事
- 赔率数据为空（早盘未开）：标记"待开盘"，不做预测

---

## 四、数据清洗：名称映射

### 4.1 三层匹配策略

| 层级 | 方法 | 说明 |
|------|------|------|
| L1 精确匹配 | 翻译词典 | 城市/地名翻译表（广州→Guangzhou，曼彻斯特→Manchester） |
| L2 模糊匹配 | 编辑距离 + 简称映射 | 皇马→Real Madrid，莱万特→Levante |
| L3 人工确认 | 前端操作界面 | 待确认队列，展示候选列表供人工选择 |

### 4.2 映射实体类型

- **联赛映射**：500.com 中文联赛名 → SportMonks 英文联赛名，含竞彩编号（如 109→英超→Premier League）
- **球队映射**：500.com 中文队名 → SportMonks 英文队名

### 4.3 多别名机制

同一实体（联赛/球队）在多个数据源中可能有不同名称，通过别名列表实现交叉匹配：
- 中文别名：曼彻斯特联 / 曼联 / 曼彻斯特联队 / 曼联队
- 英文别名：Manchester United / Man United / Man Utd / MUFC
- 任一别名命中即可关联，主名称优先展示

### 4.4 数据模型

```sql
leagues: id, sportmonks_id, name_zh(主), name_en(主), country, season, active
league_aliases: id, league_id, alias_name, source(500.com/sportmonks), is_primary

teams: id, sportmonks_id, league_id, name_zh(主), name_en(主), logo_url
team_aliases: id, team_id, alias_name, source, is_primary
```

### 4.5 持久化

双存储：`team_mapping.json`（本地文件，便于版本管理和人工编辑）+ PostgreSQL `team_aliases` 表（供查询使用）

---

## 五、数据库核心表设计

```sql
leagues: id, sportmonks_id, name_zh, name_en, country, season, active
league_aliases: id, league_id, alias_name, source, is_primary

teams: id, sportmonks_id, league_id, name_zh, name_en, logo_url
team_aliases: id, team_id, alias_name, source, is_primary

matches: id, jc_match_id(竞彩编号), league_id, home_team_id, away_team_id,
         kickoff_time, status(cancel/live/finished), handicap_line(让球数),
         home_score, away_score, half_home_score, half_away_score

odds_snapshots: id, match_id, snapshot_time, bookmaker,
                home_win, draw, away_win,
                handicap_home, handicap_line, handicap_away,
                over_odds, goal_line, under_odds

team_season_stats: id, team_id, season, league_id,
                   played, wins, draws, losses, goals_for, goals_against,
                   home_wins/draws/losses, away_wins/draws/losses,
                   clean_sheets, failed_to_score, avg_possession,
                   xG, xGA, xPTS, form(L6)

head_to_head: id, home_team_id, away_team_id, match_date,
              competition, home_score, away_score, is_neutral_venue

injuries: id, team_id, player_name, type(injury/suspension),
          reason, start_date, expected_return, status

predictions: id, match_id, model_version, created_at,
             home_prob, draw_prob, away_prob,
             handicap_home_prob, handicap_draw_prob, handicap_away_prob,
             expected_goals, over_2_5_prob, under_2_5_prob,
             score_top5_json, confidence_level(high/medium/low), is_cold_match
```

---

## 六、定时任务配置

| 任务 | 频率 | 说明 |
|------|------|------|
| 赛程同步 | 每日 09:00、12:00 | 拉取当日+未来3日竞彩赛事 |
| 赔率更新 | 每日 09:00、14:00、18:00 | 同步欧赔/亚盘变动 |
| 球队信息更新 | 每日 03:00 | 伤病/阵容/积分排名全量刷新 |
| 模型重训练 | 每周一 04:00 | 累积数据后触发 LightGBM 重训练 |

使用 APScheduler + Redis 分布式锁防重复执行。任务超时（10min）自动释放锁并告警。

---

## 七、预测模型架构

### 7.1 双模型设计

```
特征工程 Pipeline（~84 个特征）
       │
       ├────→ 模型A (LightGBM 多任务)
       │       输出A1: 胜平负概率（主胜/平/客胜）
       │       输出A2: 让球胜平负概率（让胜/让平/让负）
       │
       └────→ 模型B (Poisson Regression)
               输出B: 总进球数期望值 + 进球数分布概率

       ┌──────────────────────────────┘
       ↓
   比分推导层:
   P(比分 X:Y) ∝ P(胜负倾向) × P(总进球=X+Y) × 历史比分先验
   输出: 比分 Top5 + 各概率
```

### 7.2 特征池（11 类 ~84 个）

| 类别 | 内容 | 适用模型 |
|------|------|:--:|
| A 球队基础战力 | 近期胜率、进球/失球均值、积分趋势 | A+B |
| B 交锋 & 心理 | 历史胜负比、近3场结果、主客场拆分 | A+B |
| C 赔率信号 | 初始/即时赔率、凯利指数、离散度 | A+B |
| D 阵容 & 外部 | 伤病系数、赛程密度、休息天数差 | A+B |
| E 进攻深度 | xG、xGA、xPTS、射正率、绝佳机会 | A+B |
| F 防守 & 节奏 | 抢断、拦截、控球率、传球成功率 | A+B |
| G 时段 & 情景 | 分时段进球分布、先进球率、逆转能力 | B |
| H 进球分布 | BTTS率、大球率、场均总进球 | B |
| I 实力差衍生 | xG_diff、Power Rating、ELO评分 | A |
| J 赔率衍生 | 隐含概率、偏离度、变动速率 | A |
| K 赛程 & 动机 | 旅行距离、德比、赛季阶段、战意 | A+B |

特征入模时通过 LightGBM feature_importance 自动筛选 Top-N（约 50-60）。

### 7.3 模型管理

- MLflow 管理模型版本，每次重训练自动归档旧版本
- 复盘统计页支持版本间准确率对比
- 模型文件丢失时自动回退到上一版本

### 7.4 冷门判定

当满足以下任一条件时标记为冷门预警：
- 模型预测最大概率 < 0.40
- 赔率离散度 > 阈值（多博彩公司标准差）
- 模型概率 vs 赔率隐含概率偏差 > 15%

---

## 八、后端 API 设计

```
GET  /api/matches                  # 在售赛事列表（分页，筛选日期/联赛）
GET  /api/matches/{id}             # 单场详情（含赔率历史、预测结果）
GET  /api/matches/{id}/odds-history # 赔率变动时间序列

GET  /api/teams/{id}/radar         # 球队雷达图数据
GET  /api/teams/{id}/form          # 近期战绩

GET  /api/predictions/daily        # 当日所有预测结果
GET  /api/predictions/review       # 复盘统计数据

GET  /api/reports/daily            # 每日报告数据
GET  /api/reports/export/csv       # CSV 导出

POST /api/admin/sync-matches       # 手动触发赛程同步
POST /api/admin/sync-odds          # 手动触发赔率同步
POST /api/admin/update-teams       # 手动更新球队信息
POST /api/admin/trigger-retrain    # 手动触发模型重训练

GET  /api/admin/task-status        # 定时任务状态
GET  /api/admin/logs               # 操作日志

GET  /api/mappings/leagues         # 联赛映射列表
GET  /api/mappings/teams           # 球队映射列表
GET  /api/mappings/pending         # 待确认映射队列
POST /api/mappings/confirm         # 确认映射
POST /api/mappings/add-alias       # 添加别名
```

---

## 九、前端页面架构

### 9.1 导航结构

```
顶部导航栏（4 个一级入口 + 1 个下拉）
├── 赛事预测（默认首页）
│   └── 赛事总览 → 点击卡片 → 单场详情
├── 预测报告（独立页）
├── 复盘统计（独立页）
└── 系统管理 ▾（下拉菜单）
    ├── 任务监控
    └── 名称映射管理
        ├── 联赛映射 Tab
        └── 球队映射 Tab
```

### 9.2 赛事总览页

- 左侧：日期选择 + 联赛筛选
- 顶部：Segmented Control 三 Tab（全部赛事 / 冷门预警 / 热门推荐）
- 主体：3 列宽卡片网格
- 每张卡片含：联赛·时间 + 对阵名称 + 胜平负概率条 + 进球参考 + 比分参考 + 冷热标签
- 点击卡片进入单场详情

### 9.3 单场详情页

- 顶部：返回按钮 + 对阵概要（队徽·排名·让球信息）
- 中部左右分栏：
  - **左栏（预测底座）**：六维雷达图 + 攻防数据条 → 交锋记录 + 近期状态 → 赔率变动趋势（欧赔/亚盘）
  - **右栏（预测输出）**：
    - 板块一 [模型A]：胜平负（含赔率隐含概率对比）+ 让球胜平负
    - 板块二 [模型B]：进球数分布 + 参考比分 Top5（标注"A+B联合推导"）
- 底部：本场预测报告摘要（一句话结论 + 四项汇总 + 风险提示 + 复制/导出按钮）

### 9.4 预测报告页

- 概览统计卡片（赛事数/冷门数/置信度/模型版本）
- 冷热赛事提示区（冷门=橙，热门=绿，各附原因说明）
- 预测结果清单表格（8 列：时间/联赛/对阵/胜平负/让球/进球/比分/提示/置信度）
- 底部导出栏：复制摘要 / CSV / Excel / PDF

### 9.5 复盘统计页

- 概览卡片：累计场次 / 胜平负准确率 / 让球准确率 / 进球±1命中率 / 比分Top3命中率
- 趋势图：30 天准确率趋势（三条折线）
- 盈亏模拟：均注 100 元盈亏曲线 + 累计/回报率/胜率
- 按联赛准确率 + 按置信度分档统计 + 冷门预警复盘
- 模型版本对比表

### 9.6 名称映射管理页

- 双 Tab：联赛映射 / 球队映射
- 统计概览：总需映射 / 已自动匹配 / 待人工确认
- 联赛筛选快捷标签
- 待确认队列：左列中文名 → 候选英文名列表（按相似度排序）→ 确认/选择/手动搜索/忽略
- 已确认映射表：含别名列（可添加/移除）+ 匹配方式标签（L1/L2/L3）
- 歧义处理：同一名称匹配到多个实体时，并列展示候选供选择

### 9.7 系统管理页

- 四任务状态卡片（绿灯=正常，黄灯=空闲）
- 四手动触发按钮
- 最近 48 小时日志流（成功绿 ✓ + 警告黄 ⚠）

### 9.8 边界状态

- **无竞彩赛事**：单卡片居中显示"当日无竞彩赛事，请选择其他日期"，提供"查看明日""前往预测报告"按钮
- **数据加载中**：3 列骨架屏占位
- **模型数据不足**（< 100 场）：降级展示赔率隐含概率作为参考值，提示预计训练完成时间

---

## 十、UI 设计规范（严格执行）

> **重要：以下所有 UI 设计为本次评审的最终确认版本，开发过程中必须严格按照本规范实现。任何视觉偏离需重新评审确认。**

### 10.0 设计稿索引

所有 UI 设计稿 HTML 文件存放于 [docs/superpowers/specs/ui-mockups/](./ui-mockups/)：

| 页面 | 设计稿文件 | 确认状态 |
|------|-----------|:--:|
| 全局导航 & 系统管理子导航 | [navigation-final.html](./ui-mockups/navigation-final.html) | ✅ 方案A |
| 视觉风格（配色） | [visual-style.html](./ui-mockups/visual-style.html) | ✅ 方向一·羊皮纸 |
| 字体方案 | [typography.html](./ui-mockups/typography.html) | ✅ 搭配A·思源宋体+Inter |
| 赛事总览 | [dashboard-layout.html](./ui-mockups/dashboard-layout.html) + [dashboard-tabs.html](./ui-mockups/dashboard-tabs.html) | ✅ 宽卡片+Tab筛选 |
| 单场详情 | [match-detail-v3.html](./ui-mockups/match-detail-v3.html) | ✅ 左底座+右预测+底部报告 |
| 预测报告 | [report-page.html](./ui-mockups/report-page.html) | ✅ 独立报告页 |
| 复盘统计 | [review-page.html](./ui-mockups/review-page.html) | ✅ 纵向流布局 |
| 名称映射管理 | [mapping-v2.html](./ui-mockups/mapping-v2.html) | ✅ 双Tab+多别名 |
| 系统管理 | [admin-page.html](./ui-mockups/admin-page.html) | ✅ 任务监控+日志 |

> **开发前务必打开对应设计稿文件查看完整视觉效果。**

### 10.1 全局视觉系统

#### 配色方案：羊皮纸暖灰

| 用途 | 色值 | 说明 |
|------|------|------|
| 页面底色 | `#f5f0e8` | 全局背景 |
| 顶部导航底色 | `#ede6d9` | 导航栏背景 |
| 侧栏底色 | `#f9f6ef` | 左侧筛选/子导航背景 |
| 卡片底色 | `#ffffff` | 所有内容卡片 |
| 主文字 | `#3d3628` | 标题、重要文本 |
| 次要文字 | `#6b5e4a` | 描述文字 |
| 辅助文字 | `#937b5c` | 标签、时间、联赛名 |
| 强调色-正面 | `#2d5a3b` | 主胜、高置信度、成功状态 |
| 强调色-警告 | `#c77600` | 冷门、低置信度、待确认 |
| 强调色-中立 | `#8b5a2c` | 客胜、让平、中性状态 |
| 过渡色 | `#8b7a5c` / `#c4b99a` | 概率条次要部分 |
| 边框 | `#e5dccb` | 卡片、分隔线 |
| 边框-深 | `#d9cebc` | 导航底部分隔 |
| 冷门标签底色 | `#fff3e0` | 冷门预警标签 |
| 热门标签底色 | `#e8f0e0` | 热门推荐标签 |
| 选中高亮底色 | `#f4f0e3` | 表格行选中、概率最高项 |

#### 字体方案：思源宋体 + Inter

| 用途 | 字体 | CSS 声明 |
|------|------|----------|
| 中文标题（页面级） | 思源宋体 | `font-family: Georgia, 'Noto Serif SC', serif` |
| 中文标题（组件级） | 系统字体 | `font-family: system-ui, -apple-system, sans-serif` |
| 正文/数字/西文 | Inter | `font-family: 'Inter', system-ui, sans-serif` |
| 数字专用（tnum） | Inter | `font-feature-settings: 'tnum'` |

#### 通用组件规范

- **卡片**：`border-radius: 6px`，`border: 1px solid #e5dccb`，`box-shadow: 0 1px 2px rgba(61,54,40,0.04)`
- **主按钮**：`background: #2d5a3b; color: #fff; border-radius: 5px`
- **次按钮**：`background: #fff; color: #3d3628; border: 1px solid #d9cebc; border-radius: 5px`
- **危险/警告按钮**：`background: #fff; color: #c77600; border: 1px solid #f0d9a0`
- **概率进度条**：主胜=#2d5a3b，平局=#c4b99a，客胜=#8b7a5c，底色=#e5dccb，高度 4-6px
- **匹配层级标签**：L1 精确=#e8f0e0 底 #2d5a3b 字，L2 模糊=#f4f0e3 底 #8b7a5c 字，L3 人工=#fff3e0 底 #c77600 字
- **Tab 切换**：实现为 Segmented Control 风格，`background: #ede6d9` 容器，选中项白色底+微阴影

---

### 10.2 页面级 UI 规范（逐页确认）

#### 页面 1：赛事总览 (Dashboard.tsx)

```
┌─ 顶部导航栏 ─────────────────────────────────────────┐
│  竞彩预测系统    赛事预测  预测报告  复盘统计  系统管理▾ │
├─ 筛选区 ─────────────────────────────────────────────┤
│  [全部赛事|冷门预警|热门推荐]              最后更新:09:00│
├─ 左侧日期栏 ───┬─ 卡片网格 (3列) ────────────────────┤
│ 2026-07-23     │ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ 07-24 周五     │ │ 英超 22:00│ │ 西甲 03:00│ │ 日职 18:00│
│ 07-25 周六     │ │ 曼联vs    │ │ 巴萨vs    │ │ 浦和vs    │
│                │ │ 利物浦    │ │ 皇马      │ │ 川崎      │
│ 联赛           │ │ 主42%平28%│ │ 主38%平30%│ │ 主45%平27%│
│ 英超 · 3      │ │ [概率条]  │ │ [概率条]  │ │ [概率条]  │
│ 西甲 · 2      │ │ 2.8球2:1  │ │ 3.1球1:1  │ │ 1.9球1:0  │
│ 日职 · 4      │ │ 冷门预警  │ │           │ │           │
│ 德甲 · 2      │ └──────────┘ └──────────┘ └──────────┘
│ 法甲 · 1      │  ...更多卡片...
└────────────────┴──────────────────────────────────────┘
```

**卡片内容规范**：
- 联赛标签 + 开赛时间（10px，颜色 #937b5c）
- 对阵双方（14px，字重 700，颜色 #3d3628），中用"vs"分隔（10px，#937b5c）
- 三列概率条（flex 比例对应概率值），下方标注百分比（11px）
- 底部标签行：进球参考 + 比分参考（#f9f6ef 底），冷门预警（#fff3e0 底橙字），热门推荐（#e8f0e0 底绿字）

**空态**：无赛事时居中显示单卡片，含提示文字"当日无竞彩赛事"和"查看明日""前往预测报告"两个操作按钮。

**加载态**：3 列骨架屏（灰块占位模拟卡片结构），底部文字"正在加载赛事数据..."

---

#### 页面 2：单场详情 (MatchDetail.tsx)

```
┌─ 顶部栏 ─────────────────────────────────────────────┐
│  ← 返回赛事列表              模型 v2.4.1 · 更新于14:30│
├─ 对阵概要 ───────────────────────────────────────────┤
│     (队徽) 曼联  VS  利物浦 (队徽)                     │
│     英超第4          英超第1                           │
│     英超·第23轮·22:00·老特拉福德·竞彩编号001·让球-1    │
├─ 左侧(预测底座) ──────┬─ 右侧(预测输出) ──────────────┤
│                       │                              │
│  ▲ 六维雷达图         │  ┌ 模型A: 胜平负 & 让胜平负 ┐ │
│  进攻·防守·控球·      │  │ 主胜42.3%|平27.8%|客29.9%│ │
│  xG·状态·阵容         │  │ 赔率隐含47.6%|29.4%|27.8%│ │
│  攻1.8 防0.7 xG1.62   │  │ [概率条]                 │ │
│                       │  │ ───让球-1───              │ │
│  近期交锋 | 近期状态   │  │ 让胜18.5%|让平35.2%|让负46.3%│ │
│  利物浦2:1曼联 客负   │  └──────────────────────────┘ │
│  曼联1:1利物浦 平     │                              │
│                       │  ┌ 模型B: 进球数 & 比分推导 ┐ │
│  赔率变动趋势         │  │ 0球6% 1球17% 2球24%       │ │
│  欧赔 主2.10→2.05↓   │  │ 3球22% 4+球31%           │ │
│  欧赔 平3.40→3.50↑   │  │ 预期总进球 2.8 球         │ │
│  欧赔 客3.60→3.55↓   │  │ ───比分Top5───            │ │
│  亚盘 -0.5 0.92→0.88↓│  │ 2:1 16.2% 主胜           │ │
│  离散度0.12 分歧明显  │  │ 1:1 11.8% 平局           │ │
│                       │  └──────────────────────────┘ │
├─ 底部报告摘要 ───────────────────────────────────────┤
│  ┌ 综合预测：曼联略占优势...                    ─────┐ │
│  │ [主胜42.3%] [让负46.3%] [2.8球] [2:1·16.2%]     │ │
│  │ ⚠ 赔率离散度偏高，冷门预警，置信度71%             │ │
│  └──────────────────────────────────────────────────┘ │
│                          [复制报告]  [导出本场PDF]     │
└──────────────────────────────────────────────────────┘
```

**关键布局规则**：
- 左右分栏比例：左栏 flex:1.15（较宽），右栏 flex:0.85（较窄但信息密集）
- 模型标签：模型A=#2d5a3b 底白字，模型B=#8b5a2c 底白字，均为 9px 小标签
- 概率最高项用 #f4f0e3 背景 + 2px 强调色边框突出
- 赔率隐含概率对比以小字显示在模型概率下方，帮助识别价值
- 比分 Top5 标注"A+B联合推导"来源
- 报告摘要区域底色 #f9f6ef，与上方的 #f5f0e8 形成视觉分隔

---

#### 页面 3：预测报告 (Report.tsx)

```
┌─ 顶部 ───────────────────────────────────────────────┐
│  每日预测报告                          2026-07-23     │
├─ 概览统计 ───────────────────────────────────────────┤
│  开售12场  冷门4场  置信度78%  模型v2.4.1             │
├─ 冷热提示 ───────────────────────────────────────────┤
│  ┌ 冷:曼联vs利物浦 赔率离散0.12 ┐ ┌ 冷:切尔西vs阿森纳 ┐│
│  └ 建议观望 ───────────────────┘ └ 模型偏移18% ──────┘│
│  ┌ 热:拜仁vs多特 高置信度 ─────┐ ┌ 热:巴黎vs马赛 ────┐│
│  └ 参考价值高 ─────────────────┘ └ 参考价值高 ───────┘│
├─ 预测清单表格 ───────────────────────────────────────┤
│  时间  联赛 对阵        胜平负   让球   进球  比分  提示│
│  22:00 英超 曼联vs利物浦 主胜42%  让负46% 2-3球 2:1 冷│
│  03:00 西甲 巴萨vs皇马  主胜38%  让负42% 2-4球 1:1   │
│  ...                                                  │
├─ 操作栏 ─────────────────────────────────────────────┤
│  报告生成:09:00         [复制摘要][CSV][Excel][PDF]   │
└──────────────────────────────────────────────────────┘
```

**表格规范**：
- 表头：`background: #f9f6ef`，字重 600，颜色 #6b5e4a，字号 10px
- 数据行：字号 11-12px，行高约 36px
- 冷门行：橙色标签，热门行：绿色标签
- 置信度列：>85% 绿色，70-85% 默认色，<70% 橙色

---

#### 页面 4：复盘统计 (Review.tsx)

纵向信息流布局，自上而下：
1. 概览卡片行（5 列）：累计场次 / 胜平负准确率 / 让球准确率 / 进球±1命中率 / 比分Top3命中率
2. 趋势图 + 盈亏模拟（左右并排）
3. 按联赛准确率 + 按置信度分档（左右并排）
4. 模型版本对比表

**图表规范**：
- 趋势图三条折线：胜平负=#2d5a3b，让球=#8b5a2c，进球±1=#c4b99a
- 联赛准确率进度条：>55% 绿色，45-55% 默认色，<45% 橙色
- 盈亏曲线：正值绿色区域，负值红色区域
- 模型版本表当前版本行高亮（#f4f0e3 底）

---

#### 页面 5：名称映射管理 (Mapping.tsx)

双 Tab 结构（联赛映射 / 球队映射），共享布局框架：

**联赛映射**：
- 表格列：来源(500.com) | 竞彩ID | SportMonks主名称 | SM ID | 别名列表 | 状态 | 操作
- 待确认行：黄色背景高亮（#fffbee），操作列含下拉选择+确认按钮
- 别名列：tag 形式展示（#f4f0e3 底），末尾"+添加"按钮（#e8f0e0 底绿字）

**球队映射**：
- 上方联赛筛选快捷标签（选中项 #f4f0e3 + #2d5a3b 字）
- 别名机制提示条（#f4f0e3 底）
- 示例别名卡片：中文别名列 + 英文别名列，主别名绿色边框标记
- 歧义处理卡片：黄色边框，候选实体并列展示（选中项绿色边框）

---

#### 页面 6：系统管理-任务监控 (Admin.tsx)

- 四任务状态卡片行：绿点=正常运行，黄点=待执行
- 每卡片含：任务名 + 调度频率 + 上次执行状态/耗时 + 下次执行时间
- 四手动触发按钮行（白色大按钮，含标题+说明副文本）
- 日志流列表：成功行绿 ✓，警告行黄 ⚠，含时间戳 + 来源标签 + 描述

---

#### 页面 7：系统管理-名称映射（共用 Mapping.tsx，Tab 切换进入）

---

### 10.3 边界状态规范

| 状态 | 触发条件 | UI 表现 |
|------|----------|---------|
| 空态-无赛事 | 当日无竞彩开售 | 居中单卡片，"当日无竞彩赛事"+ 操作按钮 |
| 加载态 | API 请求中 | 3 列骨架屏（灰块占位）+ 底部加载提示文字 |
| 降级态-数据不足 | 训练数据 < 100 场 | 预测区替换为赔率隐含概率展示，标注"数据积累中" |
| 错误态 | API 请求失败 | Toast 提示错误信息，页面保留上一次成功数据 |
| 待开盘 | 赔率数据为空 | 对应赛事卡片标注"待开盘"，不做预测 |

---

### 10.4 数据不足降级展示规范

当模型训练数据不足时，单场详情预测输出区展示：

```
┌──────────────────────────────┐
│  模型训练数据不足              │
│  当前积累赛事数据 < 100 场     │
│  以下展示基于赔率隐含概率      │
│                              │
│  [主胜47.6%] [平29.4%] [客23.0%]│
│  赔率隐含                      │
│                              │
│  预计 3-5 天后可完成首次训练   │
└──────────────────────────────┘
```

此时隐藏"模型A""模型B"标签，不展示比分推导结果，报告摘要区域展示赔率隐含概率的分析替代。

---

## 十一、UI ↔ 后端数据契约

> 本章逐页分析各 UI 组件所需数据、对应 API 接口和数据库表来源，确保前后端开发可独立并行。

### 11.1 赛事总览 (Dashboard)

| UI 组件 | 所需数据 | API | 数据库 |
|----------|----------|-----|--------|
| 左侧日期列表 | 未来3日日期+每日场次数 | `GET /api/matches/dates` | matches GROUP BY date |
| 左侧联赛筛选 | 联赛名+各联赛场次数 | `GET /api/matches/leagues?date=` | leagues JOIN matches |
| 赛事卡片(3列) | 对阵/时间/联赛/胜平负概率/进球/比分/冷热标签/置信度 | `GET /api/matches?date=&league=` | matches JOIN teams JOIN predictions |
| Segmented Tab | 冷热标签筛选 | 前端过滤上述数据 | predictions.is_cold_match |

### 11.2 单场详情 (MatchDetail)

| UI 组件 | 所需数据 | API | 数据库 |
|----------|----------|-----|--------|
| 对阵概要 | 排名/积分/队徽/让球数/场地/竞彩编号 | `GET /api/matches/{id}` | matches JOIN teams JOIN team_season_stats |
| 六维雷达图 | 进攻/防守/控球/xG/状态/阵容 | `GET /api/teams/{id}/radar` | team_season_stats + injuries |
| 近期交锋 | 近6场交锋记录 | `GET /api/matches/{id}/h2h` | head_to_head |
| 近期状态 | 两队近6场W/D/L序列 | `GET /api/teams/{id}/form` | team_season_stats.form |
| 赔率趋势 | 欧赔/亚盘历史序列+离散度 | `GET /api/matches/{id}/odds-history` | odds_snapshots |
| 预测输出(4种) | 模型A/B全部概率+比分Top5 | `GET /api/predictions/{match_id}` | predictions + odds_snapshots |
| 报告摘要 | 综合结论文本+风险提示 | 同上接口(文本由后端生成) | predictions.summary_text 等 |

### 11.3 预测报告 (Report)

| UI 组件 | 所需数据 | API | 数据库 |
|----------|----------|-----|--------|
| 概览统计 | 场次/冷门数/置信度/模型版本 | `GET /api/reports/daily/summary` | matches + predictions 聚合 |
| 冷热提示 | 冷热赛事列表+原因 | 同上接口 | predictions.is_cold_match |
| 预测清单表格(8列) | 全部赛事完整预测数据 | `GET /api/reports/daily` | matches JOIN predictions |
| 导出 | 同上数据格式化为CSV/Excel/PDF | `GET /api/reports/export/{format}` | 同上 |

### 11.4 复盘统计 (Review)

| UI 组件 | 所需数据 | API | 数据库 |
|----------|----------|-----|--------|
| 概览卡片(5列) | 累计场次+四种准确率 | `GET /api/predictions/review?days=30` | predictions JOIN matches(finished) |
| 30天趋势图 | 每日准确率时间序列 | 同上接口 | 按日期 GROUP BY |
| 盈亏模拟 | 模拟投注累计盈亏 | `GET /api/predictions/pnl?days=30` | predictions + matches.actual_result |
| 按联赛/置信度 | 各联赛+各档位准确率 | 同上 review 接口 | 按 league_id/confidence_level 分组 |
| 冷门复盘 | 冷门场次/命中率/误报率 | 同上 review 接口 | predictions(is_cold_match=true) |
| 模型版本对比 | 各版本准确率+场次 | `GET /api/models/versions` | predictions 按 model_version 分组 |

### 11.5 名称映射管理 (Mapping)

| UI 组件 | 所需数据 | API | 数据库 |
|----------|----------|-----|--------|
| 统计概览 | 总数/已匹配/待确认 | `GET /api/mappings/stats?type=` | team_aliases / league_aliases |
| 待确认队列 | 中文名+候选列表(含相似度) | `GET /api/mappings/pending` | teams + SportMonks API 候选 |
| 确认匹配 | POST 映射关系 | `POST /api/mappings/confirm` | team_aliases INSERT |
| 添加别名 | POST 新别名 | `POST /api/mappings/add-alias` | team_aliases INSERT |
| 已确认映射表 | 联赛/中英名/别名/匹配方式 | `GET /api/mappings/teams?status=confirmed` | teams JOIN team_aliases |

### 11.6 系统管理 (Admin)

| UI 组件 | 所需数据 | API | 数据库 |
|----------|----------|-----|--------|
| 任务状态卡片(4个) | 各任务执行状态+耗时+下次时间 | `GET /api/admin/task-status` | task_logs |
| 手动触发按钮(4个) | POST 触发任务 | `POST /api/admin/sync-*` | 各 scheduler 模块 |
| 日志流列表 | 最近48h操作日志 | `GET /api/admin/logs?hours=48` | task_logs |

### 11.7 缺口统计

**新增 API 接口（相对原 spec 补充 9 个）**：
`/api/matches/dates`、`/api/matches/leagues`、`/api/teams/{id}/radar`、`/api/matches/{id}/h2h`、`/api/predictions/{match_id}`、`/api/reports/daily`、`/api/reports/daily/summary`、`/api/reports/export/{format}`、`/api/models/versions`

**新增数据库表**：`task_logs`（id/task_type/status/start_time/end_time/duration_ms/message）

**补充字段**：
- `predictions` 表：summary_text / key_factors / risk_warning (TEXT)
- `matches` 表：venue (场地名) / jc_serial (竞彩编号)
- 赔率隐含概率：不存库，API 层从 odds_snapshots 实时计算

---

## 十二、项目目录结构

```
ricking-03/
├── backend/
│   ├── app/
│   │   ├── collector/           # 数据采集模块
│   │   │   ├── scrapers/        #   500.com 爬虫
│   │   │   ├── sportmonks/      #   SportMonks API 客户端
│   │   │   └── cleaner.py       #   球队名称匹配 & 数据清洗
│   │   ├── predictor/           # 预测模型模块
│   │   │   ├── features.py      #   特征工程
│   │   │   ├── models/          #   模型A（胜平负+让球）、模型B（进球数）
│   │   │   └── trainer.py       #   训练 & 重训练触发
│   │   ├── reporter/            # 报告生成
│   │   │   ├── templates/       #   Jinja2 报告模板
│   │   │   └── export.py        #   CSV/Excel/PDF 导出
│   │   ├── api/                 # REST API 路由
│   │   ├── scheduler.py         # APScheduler 定时任务
│   │   └── db/                  # 数据库模型（SQLAlchemy）
│   │       ├── models.py
│   │       └── database.py
│   ├── data/                    # 本地数据存储
│   │   ├── team_mapping.json    #   球队名称映射表
│   │   └── models/              #   训练好的模型文件
│   ├── requirements.txt
│   └── main.py
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx    #   赛事总览
│   │   │   ├── MatchDetail.tsx  #   单场详情
│   │   │   ├── Report.tsx       #   预测报告
│   │   │   ├── Review.tsx       #   复盘统计
│   │   │   ├── Mapping.tsx      #   名称映射管理
│   │   │   └── Admin.tsx        #   系统管理
│   │   ├── components/
│   │   │   ├── MatchCard.tsx    #   赛事卡片
│   │   │   ├── ProbBar.tsx      #   概率进度条
│   │   │   ├── RadarChart.tsx   #   雷达图
│   │   │   ├── OddsTrend.tsx    #   赔率趋势
│   │   │   ├── Skeleton.tsx     #   骨架屏
│   │   │   └── EmptyState.tsx   #   空态卡片
│   │   ├── api/                 #   后端接口调用
│   │   └── styles/              #   全局样式
│   └── package.json
├── docker-compose.yml           # PostgreSQL + Redis + 应用
└── docs/
    └── superpowers/specs/
        └── 2026-07-23-football-prediction-design.md
```

---

## 十三、部署策略

- **一期（MVP）**：Docker Compose 本地部署，PostgreSQL + Redis + FastAPI + React SPA
- **二期**：远程服务器部署，Nginx 反代，环境变量配置切换连接串

---

## 十四、开发优先级

1. **数据层**：数据库建表 → 500.com 爬虫 → SportMonks API 客户端 → 名称映射清洗
2. **模型层**：特征工程 Pipeline → 模型 A 训练 → 模型 B 训练 → 比分推导层
3. **API 层**：FastAPI 路由 → 定时任务配置
4. **前端层**：项目搭建 → 赛事总览 → 单场详情 → 预测报告 → 复盘统计 → 映射管理 → 系统管理
5. **联调 & 部署**：前后端联调 → Docker Compose → 测试验证
