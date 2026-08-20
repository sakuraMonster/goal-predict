# Task 4 Report — MarketFlow 回测端点

## curl 调用样例

```bash
curl -X POST "http://localhost:8008/api/market-flow/backtest" ^
  -H "Content-Type: application/json" ^
  -d "{\"start_kickoff\":\"2026-08-20T00:00:00Z\",\"end_kickoff\":\"2026-08-21T00:00:00Z\",\"source\":\"manual_import\",\"overwrite\":false}"
```

## pytest 输出

```text
$ C:\Users\zhangxuejun\AppData\Local\Programs\Python\Python312\python.exe -m pytest -q
............                                             [100%]
12 passed, 12 warnings in 1.02s
```
