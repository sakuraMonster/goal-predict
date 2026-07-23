import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 10000,
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

export interface PredictionData {
  home_prob: number;
  draw_prob: number;
  away_prob: number;
  handicap_home_prob: number;
  handicap_draw_prob: number;
  handicap_away_prob: number;
  expected_goals: number;
  over_2_5_prob: number;
  goal_distribution: number[];
  score_top5_json: Array<{ score: string; prob: number; result: string }>;
  confidence_level: string;
  is_cold_match: boolean;
  summary_text: string;
  model_version: string;
}

export const getMatches = (params?: { date?: string; league_id?: number }) =>
  api.get("/matches", { params }).then((r) => r.data);

export const getMatchDetail = (id: number) =>
  api.get(`/matches/${id}`).then((r) => r.data);

export const getMatchDates = () =>
  api.get("/matches/dates").then((r) => r.data);

export const getLeagues = (date?: string) =>
  api.get("/matches/leagues", { params: { date } }).then((r) => r.data);

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

export const getDailyReport = () =>
  api.get("/reports/daily").then((r) => r.data);

export const getDailySummary = () =>
  api.get("/reports/daily/summary").then((r) => r.data);

export const getReviewSummary = (days: number = 30) =>
  api.get("/predictions/review/summary", { params: { days } }).then((r) => r.data);

export const getReviewTrend = (days: number = 30) =>
  api.get("/predictions/review/trend", { params: { days } }).then((r) => r.data);

export const getPnL = (days: number = 30) =>
  api.get("/predictions/pnl", { params: { days } }).then((r) => r.data);

export default api;
