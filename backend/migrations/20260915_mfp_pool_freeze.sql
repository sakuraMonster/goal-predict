-- MarketFlowPrediction 池化判定冻结列（2026-09-15）
-- 目的：池化分析（分池/池内优选/大小球方向）在预测写入时算一次落库，
--       查询端只读不再 runtime 重算 → 历史统计不再随代码/赔率快照变化，只有重新预测才刷新。
--
-- 直接在 psql 执行：
--   psql -U postgres -d football_prediction -h localhost -f backend/migrations/20260915_mfp_pool_freeze.sql

ALTER TABLE market_flow_predictions
    ADD COLUMN IF NOT EXISTS pool_freeze_json JSONB;

COMMENT ON COLUMN market_flow_predictions.pool_freeze_json IS
    '池化判定冻结快照，仅重新预测时刷新';

-- 存量历史行回填：SQL 无法复现口径（_pool_assign_v2/_compute_preferred_outcome 为 Python 逻辑），
-- 需调用运维接口（二选一）：
--   curl -X POST "http://<host>/api/admin/backfill-pool-freeze"          # 全量未冻结行
--   curl -X POST "http://<host>/api/admin/backfill-pool-freeze?start_date=2026-08-01&end_date=2026-09-15"

-- 校验：冻结覆盖率
-- SELECT COUNT(*) AS total, COUNT(pool_freeze_json) AS frozen FROM market_flow_predictions;
