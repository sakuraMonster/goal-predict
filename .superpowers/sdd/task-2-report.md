# Task 2 Report — MarketFlow 推理引擎 + trace

## 实现摘要

- 新增推理引擎：`backend/app/predictor/models/market_flow.py`
  - 基于 CRS 赔率升序构建比分候选池：优先识别赔率“断层”，否则取 TopK=6；候选池不足 4 个时补足到 4 个
  - 将候选池映射为总进球频次 `goals_freq`
  - 基于主客风格标签合成对局风格（防守型/均衡/大开大合），并按风格优先序列与候选进球集合交集选出 best/second，总进球排序主依据为 TTG 赔率（缺失置后），同赔率再按频次、风格优先顺序 tie-break
  - 比分 best_score 取 best_total_goals 下 CRS 最低赔率；second_score 在 A(best_total_goals 次低赔率) 与 B(second_total_goals 最低赔率) 中二选一，并按 HAD 赛果倾向、HHAD 让球赛果倾向进行 tie-break
  - 输出稳定 trace（dict/JSON 兼容），包含 `pool_rule/pool_scores/goals_freq/selected_goals/selected_scores` 以及 tie-break 过程信息

## 测试

命令：

```powershell
$env:PYTHONPATH = "E:\zhangxuejun\new-thinking\ricking-03\backend"; & "C:\Users\zhangxuejun\AppData\Local\Programs\Python\Python312\python.exe" -m pytest -q tests/test_market_flow_engine.py::test_market_flow_open_game_sample
```

输出：

```text
.                                                        [100%]
1 passed in 0.03s
```
