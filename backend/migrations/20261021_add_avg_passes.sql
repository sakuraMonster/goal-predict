-- 2026-10-21: TeamSeasonStats 新增 avg_passes（场均传球，来自 SportMonks type_id 27253）
-- 本次同期修复：xG/xGA 采集（阶段 3 选赛季 bug + fixtures xGFixture 近 N 场聚合）
ALTER TABLE team_season_stats ADD COLUMN IF NOT EXISTS avg_passes FLOAT;
