import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
  headers: { "Content-Type": "application/json" },
});

export interface MatchSummary {
  id: number;
  jc_match_id: string;
  kickoff_time: string;
  venue: string;
  handicap_line: number;
  status: string;
}

export interface KeyFactorItem {
  feature: string;
  value: string;
  impact: string;
  importance?: number;
  contribution?: number;
}

export interface ModelAAnalysis {
  method: string;
  pred_direction: string;
  pred_prob: number;
  reasoning: string;
  top_features: KeyFactorItem[];
  other_features: KeyFactorItem[];
  full_probs: Record<string, number>;
}

export interface ModelBAnalysis {
  method: string;
  lambda: number;
  over_2_5_prob: number;
  reasoning: string;
  push_factors: KeyFactorItem[];
  pull_factors: KeyFactorItem[];
  goal_distribution: number[];
}

export interface KeyFactors {
  model_a: ModelAAnalysis;
  model_b: ModelBAnalysis;
  joint_analysis: string;
}

export interface ColdCorrection {
  model_original: { home: number; draw: number; away: number };
  market_implied: { home: number; draw: number; away: number };
  blend_ratio: { model: number; market: number };
  reason: string;
}

export interface ModelCDetail {
  goal_line: number;
  calib: number;
  strength_adj: number;
  form_adj: number;
  drop_adj: number;
  lambda_raw: number;
  low_score_applied: boolean;
  low_score_factor: number | null;
  home_goals_avg: number;
  away_goals_avg: number;
  home_gf_avg_6: number;
  away_gf_avg_6: number;
  goal_drop: number;
  league_name: string | null;
}

export interface PredictionData {
  home_prob: number;
  draw_prob: number;
  away_prob: number;
  handicap_home_prob: number;
  handicap_draw_prob: number;
  handicap_away_prob: number;
  expected_goals: number;
  expected_goals_c?: number;       // Model C 市场基线预测
  snap_top2_c?: number[];          // Model C SNAP
  model_c_detail?: ModelCDetail;   // Model C 计算明细
  over_2_5_prob: number;
  goal_distribution: number[];
  score_top5_json: Array<{ score: string; prob: number; result: string }>;
  confidence_level: string;
  is_cold_match: boolean;
  cold_correction: ColdCorrection | null;
  summary_text: string;
  key_factors: KeyFactors | null;
  model_version: string;
  risk_warning?: string[];  // V4.12: 数据质量警告列表
  // 赛果
  actual_home_score?: number;
  actual_away_score?: number;
  actual_total_goals?: number;
  actual_score?: string;
  result_spf?: number;
  result_hcp?: number;
  result_goals?: number;
  result_score?: number;
}

export const getMatches = (params?: { date?: string; league_id?: number; days?: number }) =>
  api.get("/matches", { params }).then((r) => r.data);

export const getMatchDetail = (id: number) =>
  api.get(`/matches/${id}`).then((r) => r.data);

export const getMatchDates = () =>
  api.get("/matches/dates").then((r) => r.data);

export const getLeagues = (date?: string) =>
  api.get("/matches/leagues", { params: date ? { date } : {} }).then((r) => r.data);

export const getOddsHistory = (matchId: number) =>
  api.get(`/matches/${matchId}/odds-history`).then((r) => r.data);

export const getH2H = (matchId: number) =>
  api.get(`/matches/${matchId}/h2h`).then((r) => r.data);

export const getPrediction = (matchId: number) =>
  api.get(`/predictions/${matchId}`).then((r) => r.data);

export const getTeamRadar = (teamId: number) =>
  api.get(`/teams/${teamId}/radar`).then((r) => r.data);

export const getTeamForm = (teamId: number) =>
  api.get(`/teams/${teamId}/form`).then((r) => r.data);

export const getMatchTeamComparison = (matchId: number) =>
  api.get(`/teams/match/${matchId}/comparison`).then((r) => r.data);

export const getDailyReport = (date?: string) =>
  api.get("/reports/daily", { params: date ? { date } : {} }).then((r) => r.data);

export const getReportRange = (params: { date_from: string; date_to: string; league_id?: number }) =>
  api.get("/reports/range", { params }).then((r) => r.data);

export const getLeagueAccuracy = (days: number = 30) =>
  api.get("/reports/league-accuracy", { params: { days } }).then((r) => r.data);

export interface GoalPickSignal {
  type: string;
  label: string;
  acc: number;
  n: number;
  weight: number;
}

export interface GoalPick {
  match_id: number;
  match_num: string;
  league_name: string;
  home_team: string;
  away_team: string;
  kickoff_time: string;
  expected_goals_c: number;
  snap_top2_c: number[];
  score: number;
  signals: GoalPickSignal[];
}

export interface GoalPicksResponse {
  data: GoalPick[];
  secondary?: GoalPick[];   // 次选（不持久化，仅列表展示）
  date: string;
  days: number;
  top_n: number;
  global_accuracy: number;
  global_n: number;
  total_candidates: number;
}

export const getGoalPicks = (params?: { date?: string; days?: number; top_n?: number; secondary?: number }) =>
  api.get("/reports/goal-picks", { params }).then((r) => r.data);

export interface GoalPickHistoryDay {
  date: string;
  total: number;
  settled: number;
  hit: number;
  accuracy: number;
}

export interface GoalPickHistorySummary {
  total_picks: number;
  settled: number;
  hit: number;
  miss: number;
  accuracy: number;
}

export interface GoalPickHistoryResponse {
  data: GoalPickHistoryDay[];
  summary: GoalPickHistorySummary;
  days: number;
}

export const getGoalPicksHistory = (params?: { days?: number }) =>
  api.get("/reports/goal-picks/history", { params }).then((r) => r.data);

export interface ColdPick {
  match_id: number;
  match_num: string;
  league_name: string;
  home_team: string;
  away_team: string;
  kickoff_time: string;
  fav_dir: number;          // 市场热门方向 0=主胜 2=客胜
  fav_prob: number;         // 市场热门方向隐含概率
  implied_probs: {          // 去水归一化隐含概率三元组
    home: number;
    draw: number;
    away: number;
  };
  cold_dirs: number[];      // 搏冷方向（所有非热门方向，含平局）
  rank_gap: number;         // 同联赛排名差距 |home_pos - away_pos|
  odds_delta_max: number;   // 盘口资金异动：开盘→收盘最大概率变动（待验证叠加特征）
  odds_step_max: number;    // 盘口资金异动：相邻快照最大单步跳变（待验证叠加特征）
  score: number;            // 把握度评分（rank_gap 为主）
  signals: string[];
}

export interface ColdPicksResponse {
  data: ColdPick[];
  secondary?: ColdPick[];
  date: string;
  top_n: number;
  total_candidates: number;
  total_conflicts: number;
}

export const getColdPicks = (params?: { date?: string; top_n?: number; secondary?: number }) =>
  api.get("/reports/cold-picks", { params }).then((r) => r.data);

export interface ColdPickHistoryDay {
  date: string;
  total: number;
  settled: number;
  hit: number;
  accuracy: number;
}

export interface ColdPickHistorySummary {
  total_picks: number;
  settled: number;
  hit: number;
  miss: number;
  accuracy: number;
}

export interface ColdPickHistoryResponse {
  data: ColdPickHistoryDay[];
  summary: ColdPickHistorySummary;
  days: number;
}

export const getColdPicksHistory = (params?: { days?: number }) =>
  api.get("/reports/cold-picks/history", { params }).then((r) => r.data);

export const getDailySummary = (date?: string) =>
  api.get("/reports/daily/summary", { params: date ? { date } : {} }).then((r) => r.data);

export const updateResults = (date?: string) =>
  api.post("/reports/update-results", null, { params: date ? { date } : {} }).then((r) => r.data);

export const getReviewSummary = (days: number = 30) =>
  api.get("/predictions/review/summary", { params: { days } }).then((r) => r.data);

export const getReviewTrend = (days: number = 30) =>
  api.get("/predictions/review/trend", { params: { days } }).then((r) => r.data);

export const getDailyReview = (date?: string) =>
  api.get("/predictions/review/daily", { params: date ? { date } : {} }).then((r) => r.data);

export const getPnL = (days: number = 30) =>
  api.get("/predictions/pnl", { params: { days } }).then((r) => r.data);

export const getMappingStats = (type: string = "team") =>
  api.get("/mappings/stats", { params: { type } }).then((r) => r.data);

export const getLeagueMappings = (search?: string, status?: string) =>
  api.get("/mappings/leagues", { params: { ...(search ? { search } : {}), ...(status ? { status } : {}) } }).then((r) => r.data);

export const getTeamMappings = (leagueId?: number, search?: string, status?: string) =>
  api.get("/mappings/teams", { params: { ...(leagueId ? { league_id: leagueId } : {}), ...(search ? { search } : {}), ...(status ? { status } : {}) } }).then((r) => r.data);

export const getPendingMappings = (type: string = "team") =>
  api.get("/mappings/pending", { params: { type } }).then((r) => r.data);

export const confirmMapping = (payload: { type: string; id: number; name_zh?: string; name_en?: string; sportmonks_id?: number }) =>
  api.post("/mappings/confirm", payload).then((r) => r.data);

export const addAlias = (payload: { type: string; id: number; alias_name: string; source?: string }) =>
  api.post("/mappings/add-alias", payload).then((r) => r.data);

export const batchMatch = () =>
  api.post("/mappings/batch-match").then((r) => r.data);

export const predictModelC = () =>
  api.post("/admin/predict-model-c").then((r) => r.data);

export const syncOdds = () =>
  api.post("/admin/sync-odds").then((r) => r.data);

export const syncMarketFlowOdds = (params?: { date?: string }) =>
  api.post("/admin/sync-market-flow-odds", null, { params }).then((r) => r.data);

export const syncMarketFlowSmOdds = (params?: { date?: string }) =>
  api.post("/admin/sync-market-flow-sm-odds", null, { params }).then((r) => r.data);

export const updateTeams = () =>
  api.post("/admin/update-teams").then((r) => r.data);

export const repredictModelB = (date: string) =>
  api.post("/admin/repredict-model-b", null, { params: { date } }).then((r) => r.data);

export const repredictModelC = (date: string) =>
  api.post("/admin/repredict-model-c", null, { params: { date } }).then((r) => r.data);

export interface OuDirectionVerdict {
  p_big: number;
  p_small: number;
  goal_line?: number;
  direction: "over" | "under" | "skip";
  confidence: number;
  bucket: string;
  top2_ints: number[];
  tier: string;
}

export interface OuSmVerdict {
  p_big: number;
  p_small: number;
  goal_line?: number;
  direction: "over" | "under" | "skip";
  confidence: number;
  delta: number;
  n_books: number;
  source: string;
  tiers: Record<string, "over" | "under" | "skip">;
  /** 各整半线独立判定（0.5/1.5/2.5/3.5/4.5...），主判定仍为 goal_line */
  lines?: Record<string, {
    p_big: number;
    p_small: number;
    goal_line: number;
    direction: "over" | "under" | "skip";
    confidence: number;
    delta: number;
    n_books: number;
  }>;
}

export interface MarketFlowPredictionItem {
  id: number;
  match_id: number;
  match_num?: string | null;
  league_name?: string | null;
  league_id?: number | null;
  kickoff_time: string;
  home_team: string;
  away_team: string;
  model_version: string;
  is_cfusion?: boolean;
  had_pref?: string | null;
  allowed_outcomes: Array<"home" | "draw" | "away">;
  excluded_outcomes: Array<"home" | "draw" | "away">;
  preferred_outcome?: "home" | "draw" | "away" | string | null;
  enforce_excluded: string[];
  total_goals_top2: number[];
  total_goals_top3?: number[];
  score_top2: string[];
  score_top3: string[];
  actual_outcome?: string | null;
  actual_score?: string | null;
  actual_total_goals?: number | null;
  outcome_hit?: boolean | null;
  preferred_hit?: boolean | null;
  total_hit_top2?: boolean | null;
  total_hit_top3?: boolean | null;
  score_hit_top2?: boolean | null;
  score_hit_top3?: boolean | null;
  prediction_created_at?: string | null;
  snapshot_time?: string | null;
  snapshot_source?: string | null;
  pool?: "favorite" | "ambiguous" | "upset" | "unpooled" | null;
  cold_dir?: "home" | "draw" | "away" | string | null;
  cold_signal?: boolean | null;
  cold_type?: "strong" | "warn" | null;
  pref_bucket?: string | null;
  hhad_pref?: string | null;
  hhad_pref_odd?: number | null;
  fusion?: {
    source: string;
    expected_goals_c?: number | null;
    snap_top2_c?: number[] | null;
    total_goals_top3_c?: number[] | null;
  } | null;
  ou_direction?: OuDirectionVerdict | null;
  ou_hit?: boolean | null;
  ou_tier?: string | null;
  ou_sm?: OuSmVerdict | null;
  ou_sm_hit?: boolean | null;
  ou_sm_tier?: string | null;
}

export interface OuTierStats {
  signal: number;
  bet: number;
  skip: number;
  settled: number;
  hit: number;
}

export interface MarketFlowLiveSummary {
  window_start: string;
  window_end: string;
  default_date: string;
  n_predicted: number;
  n_with_results: number;
  outcome_keep_one: number;
  outcome_hit: number;
  preferred_hit?: number;
  total_top2_hit: number;
  total_top3_hit?: number;
  score_top2_hit?: number;
  score_top3_hit: number;
  ou_tier?: string;
  ou_signal?: number;
  ou_bet?: number;
  ou_skip?: number;
  ou_hit?: number;
  ou_settled?: number;
  ou_rate?: number | null;
  ou_tiers?: Record<string, OuTierStats>;
  ou_sm_tier?: string;
  ou_sm_signal?: number;
  ou_sm_bet?: number;
  ou_sm_skip?: number;
  ou_sm_hit?: number;
  ou_sm_settled?: number;
  ou_sm_rate?: number | null;
  ou_sm_tiers?: Record<string, OuTierStats>;
  model_versions: string[];
}

export const getMarketFlowLive = (params?: { date?: string; model_version?: string; source?: string; ou_tier?: string; ou_sm_tier?: string }) =>
  api.get("/market-flow/predictions/live", { params }).then((r) => r.data as { data: MarketFlowPredictionItem[]; summary: MarketFlowLiveSummary });

export interface MarketFlowHistorySummary {
  window_start: string | null;
  window_end: string | null;
  n_total: number;
  n_settled: number;
  n_pick_total?: number;
  outcome_hit_total?: number;
  preferred_hit_total?: number;
  total_goals_top2_hit_total?: number;
  total_goals_top3_hit_total?: number;
  score_top2_hit_total?: number;
  score_top3_hit_total?: number;
  ou_tier?: string;
  ou_signal?: number;
  ou_bet?: number;
  ou_skip?: number;
  ou_hit?: number;
  ou_settled?: number;
  ou_rate?: number | null;
  ou_tiers?: Record<string, OuTierStats>;
  ou_sm_tier?: string;
  ou_sm_signal?: number;
  ou_sm_bet?: number;
  ou_sm_skip?: number;
  ou_sm_hit?: number;
  ou_sm_settled?: number;
  ou_sm_rate?: number | null;
  ou_sm_tiers?: Record<string, OuTierStats>;
  model_versions: string[];
  by_model_version: Array<Record<string, any>>;
  by_date: Array<Record<string, any>>;
  by_league?: Array<Record<string, any>>;
  by_model_version_date: Array<Record<string, any>>;
}

export const getMarketFlowHistory = (params?: { start_date?: string; end_date?: string; model_version?: string; source?: string; ou_tier?: string; ou_sm_tier?: string }) =>
  api.get("/market-flow/predictions/history", { params }).then((r) => r.data as { data: MarketFlowPredictionItem[]; summary: MarketFlowHistorySummary });

// ===== 大小球方向 / O/U 盘口 独立历史报告（standard / strict 两档） =====
export interface OuHistoryStat {
  signal: number;
  bet: number;
  skip: number;
  settled: number;
  hit: number;
}
export type OuTierStatMap = Record<string, OuHistoryStat>;
export interface OuMatchTierCell {
  direction: "over" | "under" | "skip";
  hit: boolean | null;
}
export interface MarketFlowOuMatch {
  match_num?: string | null;
  kickoff_time: string;
  home_team?: string | null;
  away_team?: string | null;
  league_name?: string | null;
  actual_score?: string | null;
  ou: Record<string, OuMatchTierCell>;
  ou_sm: Record<string, OuMatchTierCell>;
}
export interface MarketFlowOuHistoryDay {
  date: string;
  n: number;
  ou: OuTierStatMap;
  ou_sm: OuTierStatMap;
  matches: MarketFlowOuMatch[];
}
export interface MarketFlowOuHistoryResponse {
  window_start: string | null;
  window_end: string | null;
  n_total: number;
  n_settled: number;
  totals: { ou: OuTierStatMap; ou_sm: OuTierStatMap };
  by_date: MarketFlowOuHistoryDay[];
}
export const getMarketFlowOuHistory = (params?: { start_date?: string; end_date?: string; model_version?: string; source?: string }) =>
  api.get("/market-flow/predictions/ou-history", { params }).then((r) => r.data as MarketFlowOuHistoryResponse);

// ===== 串关推荐（3串1 = 2场进球数3选 + 1场方向优选胜平负单关） =====
export interface MarketFlowParlayLeg {
  match_num?: string | null;
  home_team?: string | null;
  away_team?: string | null;
  league_name?: string | null;
  kickoff_time: string;
  kind: "goals" | "hafu" | "dir";
  source?: string | null;      // dir 腿来源: favorite_hafu / ambiguous_had
  pick: string;                // 进球腿: "3/4/5"；方向腿: "胜胜/负负/主/平/客"
  pref?: string | null;
  top3?: number[] | null;
  dir?: string | null;
  hafu_key?: string | null;
  exp_total?: number | null;   // 进球腿期望总进球（近6场攻防）
  p_hat: number;
  odds: number;
  keep1?: boolean | null;
  hit: boolean | null;
  actual?: string | null;
}
export interface MarketFlowParlayPick {
  matchday: string;
  pick_id?: string;            // 唯一标识（matchday + 腿场次摘要），前端列表 key 用
  combo_level?: string;        // 组合级别: strict=三腿全异联赛 / loose=宽松(两进球腿异联赛) / fallback=兜底 / default=无降级
  settled: boolean;
  parlay_odds: number;
  parlay_p_hat: number;
  stake?: number;              // 下注注数（进球3选复式=3注；方案C=1注）
  payout?: number;             // 实际返奖金额（命中按进球数单项赔率，未中=0）
  roi?: number;                // 单场ROI = payout / stake
  hit: boolean | null;
  in_range?: boolean;          // 方案C：串关赔率是否落在 [min_odds, max_odds]（false=退化组合）
  legs: MarketFlowParlayLeg[];
}
export interface MarketFlowParlayStats {
  n: number;
  hit: number;
  p_hit: number | null;
  avg_odds: number | null;
  total_stake?: number;        // 总下注注数
  total_payout?: number;       // 总返奖金额
  roi: number | null;          // 总ROI = 总返奖 / 总下注
}
export interface MarketFlowParlayResponse {
  window_start: string | null;
  window_end: string | null;
  min_odds?: number;           // 方案C：串关赔率下界
  max_odds?: number;           // 方案C：串关赔率上界
  picks: MarketFlowParlayPick[];
  stats: MarketFlowParlayStats;
}
export const getMarketFlowParlay = (params?: { date?: string; start_date?: string; end_date?: string; model_version?: string; source?: string; goals_only?: string }) =>
  api.get("/market-flow/predictions/parlay", { params }).then((r) => r.data as MarketFlowParlayResponse);

export const getMarketFlowParlayDir = (params?: { date?: string; start_date?: string; end_date?: string; model_version?: string; source?: string; min_odds?: number; max_odds?: number }) =>
  api.get("/market-flow/predictions/parlay-dir", { params }).then((r) => r.data as MarketFlowParlayResponse);

export const getMarketFlowParlayD = (params?: { date?: string; start_date?: string; end_date?: string; model_version?: string; source?: string; min_odds?: number; max_odds?: number }) =>
  api.get("/market-flow/predictions/parlay-d", { params }).then((r) => r.data as MarketFlowParlayResponse);

// ---- 方案G：halfdraw(R1半平/R2 fav侧) × 方案D方向腿，每日1串 ----
export interface ParlayFLegPlayed {
  key: string;
  zh: string;
  odds: number;
}
export interface ParlayFLeg {
  kind: "halfdraw" | "dir";
  code?: string;          // halfdraw 腿: R1 / R2
  fav?: string;
  match_num?: string;
  home_team?: string;
  away_team?: string;
  league_name?: string | null;
  kickoff_time?: string;
  played?: ParlayFLegPlayed[]; // halfdraw 腿选项(1或2格)
  stake?: number;
  pick?: string;          // dir 腿
  odds?: number;
  source?: string;
  hit?: boolean | null;
  actual?: string | null;
}
export interface ParlayFPick {
  matchday?: string;
  combo_level?: string;   // R1 / R2 / R2_fallback
  settled: boolean;
  in_range?: boolean;
  stake?: number;
  payout?: number | null;
  roi?: number | null;
  hit?: boolean | null;
  parlay_odds?: number | null;
  legs: ParlayFLeg[];
}
export interface ParlayFResponse {
  picks: ParlayFPick[];
  stats: MarketFlowParlayStats;
}
export const getMarketFlowParlayF = (params?: { date?: string; start_date?: string; end_date?: string }) =>
  api.get("/market-flow/predictions/parlay-f", { params }).then((r) => r.data as ParlayFResponse);

// ===== 方案E：每日进球双选建议 + 人工确认（与方案D方向腿组 2串1） =====
export interface ParlayEMatch {
  match_id: number;
  match_num?: string | null;
  league_name?: string | null;
  home_team?: string | null;
  away_team?: string | null;
  kickoff_time?: string | null;
  p23: number;                       // 市场隐含 P(2∪3)
  p34: number;                       // 市场隐含 P(3∪4)
  pick_odds23?: Record<string, number>;  // 买 {2,3} 两档赔率
  pick_odds34?: Record<string, number>;  // 买 {3,4} 两档赔率
  excl23?: boolean;                  // 联赛被 23 规则排除（葡超/法乙）
  excl34?: boolean;                  // 联赛被 34 规则排除（美职联/芬超/瑞典超）
}
export interface ParlayESuggestion extends ParlayEMatch {
  leg_type: "23" | "34";
  downgraded?: boolean;              // 34 缺货降级为第二场 23
  pick_odds?: Record<string, number>;
  p_hat: number;
  pick_nums: number[];
}
export interface ParlayEDirSnapshot {
  match_id: number;
  match_num?: string | null;
  league_name?: string | null;
  home_team?: string | null;
  away_team?: string | null;
  kickoff_time?: string | null;
  pick: string;
  odds: number;
  p_hat?: number | null;
  source?: string | null;
}
export interface ParlayEConfirm {
  pick_date: string;
  match_id: number;
  match_num?: string | null;
  league_name?: string | null;
  home_team?: string | null;
  away_team?: string | null;
  kickoff_time?: string | null;
  leg_type: "23" | "34";
  goal_pick_odds?: Record<string, number>;
  goal_p_hat?: number | null;
  has_dir: boolean;
  dir?: ParlayEDirSnapshot | null;
  parlay_odds_max?: number | null;
  updated_at?: string | null;
}
export interface ParlayEResponse {
  date: string;
  suggestions: ParlayESuggestion[];
  suggestion_reason?: string | null;
  day_matches: ParlayEMatch[];
  dir_options?: ParlayEDirSnapshot[];  // 当日可人工指定的方向腿候选（玩法池同方案D，排除/按 source 由前端过滤）
  confirm?: ParlayEConfirm | null;
}
export const getMarketFlowParlayE = (params: { date: string }) =>
  api.get("/market-flow/predictions/parlay-e", { params }).then((r) => r.data as ParlayEResponse);

export const confirmMarketFlowParlayE = (payload: {
  date: string;
  match_id: number;
  leg_type: "23" | "34";
  dir?: ParlayEDirSnapshot | null;
}) =>
  api.post("/market-flow/predictions/parlay-e/confirm", payload).then((r) => r.data as { success: boolean; message?: string; confirm?: ParlayEConfirm | null });

export interface ParlayEHistoryStats {
  confirm_n: number;
  goal_settled_n: number;
  goal_hit_n: number;
  goal_p_hit: number | null;
  dir_verified_n: number;
  dir_hit_n: number;
  dir_p_hit: number | null;
  combo_n: number;
  combo_hit_n: number;
  combo_p_hit: number | null;
  total_stake: number;
  total_payout: number;
  roi: number | null;
}
export interface ParlayEHistoryRow {
  pick_date: string;
  leg_type: "23" | "34";
  goal: {
    match_id: number;
    match_num?: string | null;
    league_name?: string | null;
    home_team?: string | null;
    away_team?: string | null;
    pick_odds?: Record<string, number>;
    nums: number[];
    settled: boolean;
    act?: number | null;
    hit: boolean | null;
  };
  dir?: {
    match_id?: number;
    match_num?: string | null;
    league_name?: string | null;
    home_team?: string | null;
    away_team?: string | null;
    pick: string;
    odds?: number | null;
    source?: string | null;
    verified: boolean;
    hit: boolean | null;
  } | null;
  combo_hit: boolean | null;
  parlay_odds_max?: number | null;
  stake?: number | null;
  payout?: number | null;
}
export interface ParlayEHistory {
  rows: ParlayEHistoryRow[];
  stats: ParlayEHistoryStats;
}
export const getMarketFlowParlayEHistory = () =>
  api.get("/market-flow/predictions/parlay-e/history").then((r) => r.data as ParlayEHistory);


// ===== 方案终稿：弱腿候选 + 人工确认（方案 A/D/C） =====
/** 终稿单腿选择：单选=opt_id；同场双选（方案C/D 半全场/方向弱腿）= 同一场两个 opt_id 数组 */
export type FinalChoiceSel = string | string[];
export interface FinalChoice {
  key: string;
  pick: string;
  odds: number;
  p_hat: number;
  nums?: number[];
  pick_odds?: Record<string, number>;
}
export interface FinalOption {
  opt_id: string;
  key: string;
  pick: string;
  odds: number;
  p_hat: number;
  nums?: number[];
  pick_odds?: Record<string, number>;
  dir?: string;                  // 进球腿：over/under（手工新增时同一槽位可含两种）
  match_id?: number | null;
  match_num?: string | null;
  league_name?: string | null;
  home_team?: string | null;
  away_team?: string | null;
  kickoff_time?: string | null;
  is_other: boolean;      // true = 换到其他比赛
  is_default: boolean;    // 该场系统默认档位
  bucket?: "rec1" | "rec2" | "manual";  // rec1=当前默认场 rec2=第二高置信场 manual=人工指定
}
export interface FinalLeg {
  idx: number;
  kind: "goals" | "hafu" | "dir" | "halfdraw" | string;
  source?: string;
  code?: string;                 // halfdraw 腿：R1 半平首选 / R2 半全场次选
  picks?: Array<{ key: string; pick?: string; odds?: number }>;
  match_id?: number | null;
  match_num?: string | null;
  league_name?: string | null;
  home_team?: string | null;
  away_team?: string | null;
  kickoff_time?: string | null;
  pick?: string | null;
  p_hat?: number | null;
  odds?: number | null;
  dir?: string;
  top3?: number[];
  weak?: boolean;
  weak_hit?: number | null;
  weak_threshold?: number | null;
  options?: FinalOption[];
  default_opt_id?: string | null;
  other_match_n?: number;
  hit?: boolean | null;
  actual?: string | null;
}
export interface ParlayFinalConfirm {
  pick_date: string;
  plan: string;
  system_legs: Array<Record<string, unknown>>;
  final_legs: Array<Record<string, unknown>>;
  meta?: { changed_legs?: number[]; choices?: Record<string, FinalChoiceSel>; manual?: boolean } | null;
  combo_level?: string | null;
  parlay_odds?: number | null;
  stake?: number | null;
  updated_at?: string | null;
}
export interface ParlayFinalPlanView {
  plan: string;
  exists: boolean;
  manual?: boolean;              // 系统当日无组合 → 由人工逐腿选定
  combo_level?: string | null;
  parlay_odds?: number | null;
  parlay_p_hat?: number | null;
  stake?: number | null;
  legs: FinalLeg[];
  confirm?: ParlayFinalConfirm | null;
  error?: string | null;
}
export interface ParlayFinalDayResponse {
  date: string;
  plans: Record<string, ParlayFinalPlanView>;
}
export const getMarketFlowParlayFinalDay = (params: { date: string }) =>
  api.get("/market-flow/predictions/parlay-final/day", { params }).then((r) => r.data as ParlayFinalDayResponse);

export const confirmMarketFlowParlayFinal = (payload: {
  date: string;
  plan: string;
  choices?: Record<string, FinalChoiceSel>;
}) =>
  api.post("/market-flow/predictions/parlay-final/confirm", payload).then(
    (r) => r.data as { status?: string; error?: string; confirm?: ParlayFinalConfirm | null }
  );

export interface ParlayFinalPlanStats {
  confirm_n: number;
  settled_n: number;
  default_p_hit: number | null;
  final_p_hit: number | null;
  same_n: number;
  improved_n: number;
  worsened_n: number;
  manual_n?: number;
  manual_settled_n?: number;
  manual_p_hit?: number | null;
  default_roi: number | null;
  final_roi: number | null;
}
export interface ParlayFinalHistoryStats {
  confirm_n: number;
  by_plan?: Record<string, ParlayFinalPlanStats>;
  changed_legs?: { n: number; default_p_hit: number | null; final_p_hit: number | null };
}
/** 终稿历史中逐日的腿（系统默认 / 终稿，均带逐腿命中）——供方案列表里与原方案分开展示 */
export interface ParlayFinalHistLeg {
  kind?: string;
  source?: string;
  match_id?: number | null;
  match_num?: string | null;
  league_name?: string | null;
  home_team?: string | null;
  away_team?: string | null;
  kickoff_time?: string | null;
  pick?: string | null;
  odds?: number | null;
  p_hat?: number | null;
  dir?: string;
  top3?: number[];
  hafu_key?: string;
  pref?: string;
  picks?: Array<{ key: string; pick?: string; odds?: number }>;
  hit?: boolean | null;
  actual?: string | null;
}
export interface ParlayFinalHistoryRow {
  pick_date: string;
  plan: string;
  manual?: boolean;              // 该系统当日无组合，由人工逐腿选定
  changed_legs: Array<Record<string, unknown>>;
  default_legs?: ParlayFinalHistLeg[];
  final_legs?: ParlayFinalHistLeg[];
  default: { hit: boolean | null; settled: boolean; exists?: boolean; payout?: number | null; stake?: number | null };
  final: { hit: boolean | null; settled: boolean; payout?: number | null; stake?: number | null; parlay_odds?: number | null };
}
export interface ParlayFinalHistory {
  rows: ParlayFinalHistoryRow[];
  stats: ParlayFinalHistoryStats;
}
export const getMarketFlowParlayFinalHistory = () =>
  api.get("/market-flow/predictions/parlay-final/history").then((r) => r.data as ParlayFinalHistory);


// 方案生成就绪度：说明当日各方案能否生成、缺什么数据（只读）
export interface MarketFlowPlanReadiness {
  date: string;
  matches: number;
  hafu_available: number;
  ttg_signal: number;
  sm_signal: number;
  goals_signal: number;
  pools: { favorite: number; ambiguous: number; upset: number };
  plans: {
    plan_a: { ok: boolean; reason: string | null };
    plan_d: { ok: boolean; reason: string | null };
    plan_c: { ok: boolean; reason: string | null };
  };
}
export const getPlanReadiness = (params: { date: string }) =>
  api.get("/market-flow/predictions/plan-readiness", { params }).then((r) => r.data as MarketFlowPlanReadiness);

export interface MarketFlowPoolStat {
  key: "favorite" | "ambiguous" | "upset" | "unpooled";
  label: string;
  desc: string;
  n: number;
  hit: number;
  rate: number | null;
  both_hit: number;
  both_rate: number | null;
  cold_sig_n: number;
  cold_sig_hit: number;
  cold_sig_rate: number | null;
  strong_n: number;
  strong_hit: number;
  strong_rate: number | null;
  warn_n: number;
  warn_hit: number;
  warn_rate: number | null;
  today_n: number;
}

export interface PoolDayStat {
  n: number;
  hit: number;
  both_hit: number;
  cold_hit: number;
  cold_n: number;
  strong_n: number;
  strong_hit: number;
  warn_n: number;
  warn_hit: number;
}

export interface MarketFlowPoolsTrendDay {
  date: string;
  favorite: PoolDayStat;
  ambiguous: PoolDayStat;
  upset: PoolDayStat;
  unpooled: PoolDayStat;
}

export interface MarketFlowPoolsResponse {
  date: string;
  days: number;
  trend_days: number;
  window: { start: string; end: string };
  pools: MarketFlowPoolStat[];
  today: {
    favorite: MarketFlowPredictionItem[];
    ambiguous: MarketFlowPredictionItem[];
    upset: MarketFlowPredictionItem[];
    unpooled: MarketFlowPredictionItem[];
  };
  trend: MarketFlowPoolsTrendDay[];
}

export const getMarketFlowPools = (params?: { date?: string; days?: number; trend_days?: number }) =>
  api.get("/market-flow/predictions/pools", { params }).then((r) => r.data as MarketFlowPoolsResponse);

export interface MarketFlowModelVersionItem {
  model_version: string;
  count: number;
  min_kickoff?: string | null;
  max_kickoff?: string | null;
}

export const getMarketFlowModelVersions = () =>
  api.get("/market-flow/predictions/model-versions").then((r) => r.data as { data: MarketFlowModelVersionItem[] });

export const predictMarketFlow = (params?: { date?: string; overwrite?: boolean }) =>
  api.post("/admin/predict-market-flow", null, { params }).then((r) => r.data);

export default api;
