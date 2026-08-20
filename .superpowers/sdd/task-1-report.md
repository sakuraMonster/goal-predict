# Task 1 Report — MarketFlow 数据模型与迁移脚本

## Progress

- 为 teams 表新增 Team.style_tag（String(20)）
- 新增独立表 jczq_play_odds_snapshots（竞彩四玩法赔率快照最小结构）
- 新增独立表 market_flow_predictions（MarketFlow 预测结果最小结构，match_id unique）
- 新增一次性迁移脚本 backend/tools/migrate_market_flow.py（可重复执行：ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT EXISTS）
- 新增最小单测 backend/tests/test_market_flow_models.py（仅验证 ORM 定义与导入）

## Files Changed

- backend/app/db/models.py
- backend/tools/migrate_market_flow.py
- backend/tests/test_market_flow_models.py

## Commands Run

- 导入验证（按 brief）
  - $env:PYTHONPATH = "E:\zhangxuejun\new-thinking\ricking-03\backend"; & "C:\Users\zhangxuejun\AppData\Local\Programs\Python\Python312\python.exe" -c "from app.db.models import Team, JczqPlayOddsSnapshot, MarketFlowPrediction; print('ok')"
- 迁移脚本执行（按 brief）
  - $env:PYTHONPATH = "E:\zhangxuejun\new-thinking\ricking-03\backend"; & "C:\Users\zhangxuejun\AppData\Local\Programs\Python\Python312\python.exe" tools\migrate_market_flow.py
- 运行单测（限定 tests/，避免 tools/ 下的实验脚本被 pytest 收集）
  - $env:PYTHONPATH = "E:\zhangxuejun\new-thinking\ricking-03\backend"; & "C:\Users\zhangxuejun\AppData\Local\Programs\Python\Python312\python.exe" -m pytest tests -q

## Test Results

- pytest: 3 passed

## Commits

- feat: add marketflow db models and migration script
