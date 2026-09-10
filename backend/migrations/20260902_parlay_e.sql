-- 方案E（MarketFlow V2 串关推荐新增）：每日人工确认的进球场（23/34复式）与当日方案D方向腿 → 2串1
-- 只读引用方案D输出快照，不修改方案D自身逻辑
CREATE TABLE IF NOT EXISTS parlay_e_confirms (
    id               SERIAL PRIMARY KEY,
    pick_date        DATE NOT NULL,
    match_id         INTEGER NOT NULL REFERENCES matches(id),
    match_num        VARCHAR(50),
    league_name      VARCHAR(100),
    home_team        VARCHAR(100),
    away_team        VARCHAR(100),
    kickoff_time     TIMESTAMP,
    leg_type         VARCHAR(10) NOT NULL,          -- '23' = 总进球 2∪3 复式; '34' = 3∪4 复式
    goal_pick_odds   JSON,                          -- {档位: 赔率} 如 {2:3.9, 3:3.6}
    goal_p_hat       DOUBLE PRECISION,
    dir_match_id     INTEGER,
    dir_match_num    VARCHAR(50),
    dir_league_name  VARCHAR(100),
    dir_home_team    VARCHAR(100),
    dir_away_team    VARCHAR(100),
    dir_kickoff_time TIMESTAMP,
    dir_pick         VARCHAR(20),                   -- 主/客/平 或 胜胜/负负
    dir_odds         DOUBLE PRECISION,
    dir_p_hat        DOUBLE PRECISION,
    dir_source       VARCHAR(30),                   -- favorite_hafu / ambiguous_had
    parlay_odds_max  DOUBLE PRECISION,
    created_at       TIMESTAMP DEFAULT now(),
    updated_at       TIMESTAMP DEFAULT now(),
    CONSTRAINT uq_parlay_e_pick_date UNIQUE (pick_date)
);
CREATE INDEX IF NOT EXISTS ix_parlay_e_confirms_pick_date ON parlay_e_confirms (pick_date);
CREATE INDEX IF NOT EXISTS ix_parlay_e_confirms_match_id ON parlay_e_confirms (match_id);
