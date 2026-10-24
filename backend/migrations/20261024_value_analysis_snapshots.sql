-- 2026-10-24: 价值分析持久化快照表
-- 每场一个（最新一次「计算」结果），查询端只读不再现算，避免历史统计随赔率快照漂移
CREATE TABLE IF NOT EXISTS value_analysis_snapshots (
    id SERIAL PRIMARY KEY,
    match_id INTEGER NOT NULL UNIQUE REFERENCES matches(id),
    matchday_date DATE,
    analysis_json JSONB NOT NULL,
    computed_at TIMESTAMP,
    updated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_va_snapshots_matchday ON value_analysis_snapshots (matchday_date);
