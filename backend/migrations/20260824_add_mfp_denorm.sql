-- MarketFlowPrediction 补三列冗余 + 历史回填
-- 本机 Postgres: postgres/postgres@localhost:5432/football_prediction
-- 直接在 psql 执行：
--   psql -U postgres -d football_prediction -h localhost -f backend/migrations/20260824_add_mfp_denorm.sql
-- 或用 Python asyncpg 跑一次。

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'market_flow_predictions' AND column_name = 'league_id'
    ) THEN
        ALTER TABLE market_flow_predictions
            ADD COLUMN league_id INTEGER REFERENCES leagues(id);
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_market_flow_predictions_league_id
            ON market_flow_predictions (league_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'market_flow_predictions' AND column_name = 'kickoff_time'
    ) THEN
        ALTER TABLE market_flow_predictions
            ADD COLUMN kickoff_time TIMESTAMP WITHOUT TIME ZONE;
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_market_flow_predictions_kickoff_time
            ON market_flow_predictions (kickoff_time);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'market_flow_predictions' AND column_name = 'matchday_date'
    ) THEN
        ALTER TABLE market_flow_predictions
            ADD COLUMN matchday_date DATE;
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_market_flow_predictions_matchday_date
            ON market_flow_predictions (matchday_date);
    END IF;
END $$;

-- 一次性回填：从 matches 抄 league_id + kickoff_time；matchday_date = kickoff_time -12h 的 date
UPDATE market_flow_predictions mfp
   SET league_id     = m.league_id,
       kickoff_time  = m.kickoff_time,
       matchday_date = (m.kickoff_time - INTERVAL '12 hours')::DATE
  FROM matches m
 WHERE mfp.match_id = m.id
   AND (mfp.league_id IS NULL OR mfp.kickoff_time IS NULL OR mfp.matchday_date IS NULL);

-- 校验：统计回填行数 & 空值数
-- SELECT
--   COUNT(*) AS total,
--   COUNT(*) FILTER (WHERE league_id     IS NOT NULL) AS league_ok,
--   COUNT(*) FILTER (WHERE kickoff_time  IS NOT NULL) AS kickoff_ok,
--   COUNT(*) FILTER (WHERE matchday_date IS NOT NULL) AS matchday_ok
-- FROM market_flow_predictions;
