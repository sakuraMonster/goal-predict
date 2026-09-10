-- 方案终稿人工确认（2026-09-03）
-- 方案 A/D/C 弱腿（判大进球3选 / 半全场腿 / 冷门方向腿）候选 + 人工确认 → 终稿组合
CREATE TABLE IF NOT EXISTS parlay_final_confirms (
    id SERIAL PRIMARY KEY,
    pick_date DATE NOT NULL,
    plan VARCHAR(4) NOT NULL,
    system_legs JSONB,
    final_legs JSONB,
    meta JSONB,
    parlay_odds DOUBLE PRECISION,
    stake INTEGER,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT uq_parlay_final_date_plan UNIQUE (pick_date, plan)
);
CREATE INDEX IF NOT EXISTS ix_parlay_final_pick_date ON parlay_final_confirms (pick_date);
