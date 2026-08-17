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

export const updateTeams = () =>
  api.post("/admin/update-teams").then((r) => r.data);

export const repredictModelB = (date: string) =>
  api.post("/admin/repredict-model-b", null, { params: { date } }).then((r) => r.data);

export const repredictModelC = (date: string) =>
  api.post("/admin/repredict-model-c", null, { params: { date } }).then((r) => r.data);

export default api;
