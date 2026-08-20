# Task 3 Report — MarketFlow API

## 实现摘要

- 新增 MarketFlow 独立 API：`backend/app/api/market_flow.py`
- 实现两个端点：
  - `POST /api/market-flow/odds-snapshots`：写入 `jczq_play_odds_snapshots`
  - `POST /api/market-flow/predict/{match_id}`：读取快照 + Team.style_tag，调用 `MarketFlowEngine`，upsert 写入 `market_flow_predictions` 并返回结果与 trace
- 在 `backend/main.py` 注册路由：`app.include_router(market_flow.router)`

## 启动与手工验证

后端启动：

```bash
cd backend
$env:PYTHONPATH="e:\zhangxuejun\new-thinking\ricking-03\backend"
C:\Users\zhangxuejun\AppData\Local\Programs\Python\Python312\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8008 --reload
```

1) 导入赔率快照（返回 odds_snapshot_id）：

```bash
curl -X POST "http://localhost:8008/api/market-flow/odds-snapshots" ^
  -H "Content-Type: application/json" ^
  -d "{\"match_id\": 1, \"snapshot_time\": \"2026-08-20T10:00:00Z\", \"source\": \"manual_import\", \"had\": {\"home\": 1.90, \"draw\": 3.20, \"away\": 3.60}, \"hhad\": {\"line\": -1.0, \"home\": 3.10, \"draw\": 3.40, \"away\": 2.10}, \"ttg\": {\"2\": 3.50, \"3\": 3.60}, \"crs\": {\"1-0\": 7.50, \"2-1\": 8.00}}"
```

2) 单场预测（返回 best/second 与 trace）：

```bash
curl -X POST "http://localhost:8008/api/market-flow/predict/1" ^
  -H "Content-Type: application/json" ^
  -d "{\"odds_snapshot_id\": 1, \"model_version\": \"marketflow_v1\"}"
```

## 测试与结果

执行命令：

```bash
cd backend
$env:PYTHONPATH="e:\zhangxuejun\new-thinking\ricking-03\backend"
C:\Users\zhangxuejun\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q
```

结果：进程退出码 0（通过）。
