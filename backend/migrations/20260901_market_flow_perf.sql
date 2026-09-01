-- MarketFlow V2 统计接口性能优化：补齐缺失索引
-- 应用方式（psql 每条语句自动提交，CREATE INDEX CONCURRENTLY 不能在事务块内）：
--   psql -U postgres -d football_prediction -h localhost -f backend/migrations/20260901_market_flow_perf.sql

-- 1) matches.kickoff_time：_market_flow_query 的 WHERE 时间窗过滤列，原无索引（matches 19.6k 行顺序扫描）
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_matches_kickoff_time
    ON matches (kickoff_time);

-- 2) market_flow_predictions.model_version：WHERE 过滤列（IN 过滤），原无索引
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_market_flow_predictions_model_version
    ON market_flow_predictions (model_version);

-- 3) jczq_play_odds_snapshots.match_id：模型声明 index=True 但 DB 中未创建（create_all 不会给已存在的表补索引）
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jczq_play_odds_snapshots_match_id
    ON jczq_play_odds_snapshots (match_id);

-- 4) odds_snapshots：(match_id, bookmaker, goal_line, snapshot_time DESC) 复合索引经实测无效——
--    规划器对该表 45% 选择率的 IN 列表始终选择并行 Seq Scan + 外部排序，复合索引不被使用，
--    且新增 92MB 存储与写放大。O/U 批量查询改为 DISTINCT ON 只取最新快照（减少 10~40 倍传输行数），
--    配合 Redis 统计缓存（backend/app/db/redis_client.py + _market_flow_query）解决重复点击慢的问题，
--    不再需要复合索引。

-- 校验（应用后执行）：
-- SELECT indexname FROM pg_indexes
--  WHERE tablename IN ('matches','market_flow_predictions','jczq_play_odds_snapshots','odds_snapshots')
--   AND indexname IN ('ix_matches_kickoff_time','ix_market_flow_predictions_model_version',
--                     'ix_jczq_play_odds_snapshots_match_id');
