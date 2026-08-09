import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  getMatches, getMatchDates, getLeagues, getPrediction, getH2H, getMatchTeamComparison,
  getReportRange, getLeagueAccuracy, repredictModelB, predictModelC, syncOdds, updateTeams
} from "../api/client";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";
import SkeletonCard from "../components/Skeleton";
import LeagueAccuracyBarChart from "../components/LeagueAccuracyBarChart";
import { useToast } from "../components/Toast";
import type { PredictionData, KeyFactors } from "../api/client";

type GoalTab = "live" | "history";
type RangeKey = "today" | "3d" | "7d" | "30d";

const RANGE_LABELS: Record<RangeKey, string> = {
  today: "近1天", "3d": "近3天", "7d": "近7天", "30d": "近30天",
};

function getRangeDays(r: RangeKey): number {
  if (r === "today") return 1;
  if (r === "3d") return 3;
  if (r === "7d") return 7;
  return 30;
}

function getDateRange(days: number): string[] {
  const dates: string[] = [];
  const d = new Date();
  d.setDate(d.getDate() - 1); // 从昨天开始
  for (let i = 0; i < days; i++) {
    dates.push(d.toISOString().slice(0, 10));
    d.setDate(d.getDate() - 1);
  }
  return dates;
}

// ==================== SNAP 辅助 ====================
const SNAP_DOWN = 0.05;
const SNAP_UP = 0.93;
function snapTop2(lambda: number): [number, number] {
  if (lambda <= 0) return [0, 0];
  const frac = lambda - Math.floor(lambda);
  let eff = lambda;
  if (frac < SNAP_DOWN) eff = Math.max(0, Math.floor(lambda) - 1);
  else if (frac > SNAP_UP) eff = Math.min(6, Math.ceil(lambda) + 1);
  // 找距离 effective 最近的 2 个整数
  const dists = Array.from({ length: 7 }, (_, i) => [i, Math.abs(eff - i)] as [number, number]);
  dists.sort((a, b) => a[1] - b[1]);
  const a = Math.min(dists[0][0], dists[1][0]);
  const b = Math.max(dists[0][0], dists[1][0]);
  return [a, b];
}

function confidenceColor(level: string): string {
  if (level === "high") return "bg-moss";
  if (level === "medium") return "bg-amber";
  return "bg-[#9e9e9e]";
}
function confidenceTextColor(level: string): string {
  if (level === "high") return "text-moss";
  if (level === "medium") return "text-amber";
  return "text-[#9e9e9e]";
}
function confidencePercent(level: string): number {
  if (level === "high") return 85;
  if (level === "medium") return 65;
  return 45;
}

// ==================== 主组件 ====================
export default function GoalsPrediction() {
  const [activeTab, setActiveTab] = useState<GoalTab>("live");

  return (
    <div className="p-4">
      <div className="flex items-center gap-4 mb-4">
        <h1 className="text-lg font-bold font-heading tracking-wide text-ink">进球数预测管理</h1>
        <div className="flex gap-0.5 bg-parchment-dark rounded p-0.5 font-body text-xs">
          {(["live", "history"] as const).map((key) => (
            <span
              key={key}
              className={`px-3.5 py-1.5 rounded cursor-pointer transition-colors ${
                activeTab === key ? "bg-white text-ink font-semibold shadow-sm" : "text-ink-muted"
              }`}
              onClick={() => setActiveTab(key as GoalTab)}
            >
              {key === "live" ? "实时预测" : "历史报告"}
            </span>
          ))}
        </div>
      </div>
      {activeTab === "live" ? <LivePredictionView /> : <HistoryReportView />}
    </div>
  );
}

// ==================== 实时预测 ====================
interface MatchItem {
  id: number;
  match_num?: string;
  league_name: string;
  league_id: number | null;
  kickoff_time: string;
  home_team: string;
  away_team: string;
  home_prob: number;
  draw_prob: number;
  away_prob: number;
  expected_goals: number;
  expected_goals_c?: number;       // Model C 市场基线预测
  snap_top2_c?: number[];          // Model C SNAP
  snap_top2?: number[];            // Model B SNAP
  is_cold_match: boolean;
  is_hot_match: boolean;
  confidence_level: string;
  reference_score?: string;
  risk_warning?: string[] | null;
}

interface DetailData {
  prediction: PredictionData | null;
  h2h: any[];
  comparison: any | null;
}

function LivePredictionView() {
  const [matches, setMatches] = useState<MatchItem[]>([]);
  const [leagues, setLeagues] = useState<any[]>([]);
  const [dates, setDates] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState(new Date().toISOString().slice(0, 10));
  const [selectedLeague, setSelectedLeague] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<"time" | "confidence">("time");
  const [selectedMatchId, setSelectedMatchId] = useState<number | null>(null);
  const [detail, setDetail] = useState<DetailData>({ prediction: null, h2h: [], comparison: null });
  const [detailLoading, setDetailLoading] = useState(false);
  const [predicting, setPredicting] = useState(false);
  const [syncingOdds, setSyncingOdds] = useState(false);
  const [syncingTeams, setSyncingTeams] = useState(false);
  const [dateOpen, setDateOpen] = useState(false);
  const dateRef = useRef<HTMLDivElement>(null);
  const toast = useToast();

  const fetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    const params: any = { date: selectedDate, limit: 50 };
    if (selectedLeague) params.league_id = selectedLeague;

    Promise.allSettled([
      getMatches(params),
      getLeagues(selectedDate),
      getMatchDates(),
    ]).then(([mRes, lRes, dRes]) => {
      if (mRes.status === "fulfilled") setMatches(mRes.value.data || []);
      else setError("比赛数据加载失败");
      if (lRes.status === "fulfilled") setLeagues(lRes.value.data || []);
      if (dRes.status === "fulfilled") setDates(dRes.value.data || []);
    }).finally(() => setLoading(false));
  }, [selectedDate, selectedLeague]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // 日期切换时重置联赛筛选和详情
  useEffect(() => { setSelectedLeague(null); setSelectedMatchId(null); }, [selectedDate]);

  // 点击外部关闭日期下拉
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dateRef.current && !dateRef.current.contains(e.target as Node)) {
        setDateOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handlePredict = async () => {
    setPredicting(true);
    try {
      const res = await predictModelC();
      toast.toast(res.message || "Model C 预测完成", "success");
      fetchData();
    } catch {
      toast.toast("Model C 预测失败", "error");
    } finally {
      setPredicting(false);
    }
  };

  const handleSyncOdds = async () => {
    setSyncingOdds(true);
    try {
      const res = await syncOdds();
      toast.toast(res.message || "赔率更新完成", "success");
    } catch {
      toast.toast("赔率更新失败", "error");
    } finally {
      setSyncingOdds(false);
    }
  };

  const handleSyncTeams = async () => {
    setSyncingTeams(true);
    try {
      const res = await updateTeams();
      toast.toast(res.message || "球队信息更新已提交", "success");
    } catch {
      toast.toast("球队信息更新失败", "error");
    } finally {
      setSyncingTeams(false);
    }
  };

  // 加载详情
  const openDetail = async (matchId: number) => {
    setSelectedMatchId(matchId);
    setDetailLoading(true);
    try {
      const [pRes, hRes, cRes] = await Promise.allSettled([
        getPrediction(matchId), getH2H(matchId), getMatchTeamComparison(matchId),
      ]);
      setDetail({
        prediction: pRes.status === "fulfilled" ? pRes.value.data : null,
        h2h: hRes.status === "fulfilled" ? (hRes.value.data || []) : [],
        comparison: cRes.status === "fulfilled" ? cRes.value.data : null,
      });
    } finally {
      setDetailLoading(false);
    }
  };

  const closeDetail = () => { setSelectedMatchId(null); setDetail({ prediction: null, h2h: [], comparison: null }); };

  // 筛选 + 排序
  const filtered = useMemo(() => {
    let list = [...matches];
    if (selectedLeague) list = list.filter(m => m.league_id === selectedLeague);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(m =>
        m.home_team.toLowerCase().includes(q) ||
        m.away_team.toLowerCase().includes(q) ||
        m.league_name.toLowerCase().includes(q)
      );
    }
    if (sortBy === "time") {
      list.sort((a, b) => a.kickoff_time.localeCompare(b.kickoff_time));
    } else {
      const score = (m: MatchItem) => confidencePercent(m.confidence_level);
      list.sort((a, b) => score(b) - score(a));
    }
    return list;
  }, [matches, selectedLeague, search, sortBy]);

  const selectedMatch = matches.find(m => m.id === selectedMatchId);

  // 骨架屏
  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-10 bg-parchment-dark rounded-lg animate-pulse" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      </div>
    );
  }

  if (error) return <ErrorState message={error} onRetry={fetchData} />;

  return (
    <div className="space-y-4">
      {/* 监控条 */}
      <div className="flex items-center gap-3 px-4 py-3 bg-parchment-dark rounded-lg border border-border-dark">
        <span className="flex items-center gap-2 text-xs text-moss font-semibold">
          <span className="w-2 h-2 rounded-full bg-moss animate-pulse" />
          实时监控中
        </span>
        <span className="text-xs text-ink-muted">{selectedDate} · 共 {matches.length} 场比赛</span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={handleSyncOdds}
            disabled={syncingOdds}
            className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss transition-colors disabled:opacity-50"
          >
            {syncingOdds ? "更新中..." : "更新盘口"}
          </button>
          <button
            onClick={handleSyncTeams}
            disabled={syncingTeams}
            className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss transition-colors disabled:opacity-50"
          >
            {syncingTeams ? "更新中..." : "更新球队"}
          </button>
          <span className="w-px h-5 bg-border" />
          <button
            onClick={handlePredict}
            disabled={predicting}
            className="px-4 py-1.5 text-xs bg-moss text-white rounded hover:opacity-90 disabled:opacity-50 transition-opacity font-body"
          >
            {predicting ? "预测中..." : "预测"}
          </button>
          <button
            onClick={fetchData}
            className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss transition-colors"
          >
            刷新
          </button>
        </div>
      </div>

      {/* 日期选择 + 筛选栏 */}
      <div className="flex items-center gap-3 px-3 py-2.5 bg-white rounded-md border border-border flex-wrap">
        {/* 日期选择下拉 */}
        <div className="relative" ref={dateRef}>
          <button
            onClick={() => setDateOpen(!dateOpen)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs border border-border rounded bg-parchment-light hover:border-moss transition-colors"
          >
            <span className="text-ink font-semibold">{selectedDate}</span>
            <span className="text-ink-light text-[10px]">{dateOpen ? "▲" : "▼"}</span>
          </button>
          {dateOpen && (
            <div className="absolute top-full left-0 mt-1 w-[200px] max-h-[280px] overflow-y-auto bg-white border border-border rounded-md shadow-lg z-50">
              {dates.map((d: any) => (
                <div
                  key={d.date}
                  className={`flex items-center justify-between px-3 py-2 text-xs cursor-pointer hover:bg-parchment-light transition-colors ${
                    d.date === selectedDate ? "bg-parchment text-moss font-semibold" : "text-ink"
                  }`}
                  onClick={() => { setSelectedDate(d.date); setDateOpen(false); }}
                >
                  <span>{d.date}</span>
                  <span className="flex items-center gap-1.5">
                    <span className="text-ink-muted text-[10px]">{d.count}场</span>
                    {d.is_past ? (
                      <span className="text-[10px] px-1 py-0.5 rounded bg-parchment-dark text-ink-light">历史</span>
                    ) : (
                      <span className="text-[10px] px-1 py-0.5 rounded bg-hot-bg text-moss">进行中</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
        <span className="w-px h-5 bg-border" />
        <div className="flex items-center gap-2 px-2.5 py-1.5 bg-parchment-light border border-border rounded min-w-[220px]">
          <span className="text-xs text-ink-light">🔍</span>
          <input type="text" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="搜索球队或联赛..."
            className="bg-transparent outline-none text-xs text-ink w-full font-body" />
        </div>
        <span className="w-px h-5 bg-border" />
        <span className={`px-3 py-1 text-xs rounded-full cursor-pointer transition-colors ${!selectedLeague ? "bg-moss text-white" : "border border-border text-ink-muted hover:border-moss"}`}
          onClick={() => setSelectedLeague(null)}>全部</span>
        {leagues.map((l: any) => (
          <span key={l.id ?? l.name}
            className={`px-3 py-1 text-xs rounded-full cursor-pointer transition-colors ${l.id === selectedLeague ? "bg-moss text-white" : "border border-border text-ink-muted hover:border-moss"}`}
            onClick={() => setSelectedLeague(l.id === selectedLeague ? null : l.id)}>
            {l.name}{l.count ? ` ${l.count}` : ""}
          </span>
        ))}
        <span className="w-px h-5 bg-border" />
        <span className={`px-2.5 py-1 text-xs rounded cursor-pointer ${sortBy === "time" ? "bg-ink text-white" : "text-ink-muted border border-border"}`}
          onClick={() => setSortBy("time")}>⏱ 开赛时间</span>
        <span className={`px-2.5 py-1 text-xs rounded cursor-pointer ${sortBy === "confidence" ? "bg-ink text-white" : "text-ink-muted border border-border"}`}
          onClick={() => setSortBy("confidence")}>📊 置信度</span>
        <span className="ml-auto text-xs text-ink-muted">共 <strong className="text-ink">{filtered.length}</strong> 场</span>
      </div>

      {/* 空数据 */}
      {filtered.length === 0 && (
        <EmptyState icon="⚽" message={search ? "未找到匹配的比赛" : "当日无竞彩赛事"}
          description={search ? "请尝试其他关键词或切换联赛筛选" : "今日暂无比赛数据"}
          actionLabel={search ? "清除搜索" : undefined}
          onAction={search ? (() => setSearch("")) : undefined} />
      )}

      {/* 卡片网格 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {filtered.map(m => {
          return (
            <div key={m.id}
              className={`bg-white rounded-lg border p-4 cursor-pointer transition-all hover:shadow-md hover:-translate-y-0.5 ${
                selectedMatchId === m.id ? "border-moss shadow-[0_0_0_2px_rgba(45,90,59,0.15)]" : "border-border shadow-sm"
              }`}
              onClick={() => openDetail(m.id)}>
              {/* 头部：编号 + 联赛 + 时间 + 标签 */}
              <div className="flex items-center gap-2 mb-3">
                {m.match_num && <span className="text-[10px] font-bold text-ink-muted bg-parchment-dark px-1.5 py-0.5 rounded flex-shrink-0">{m.match_num}</span>}
                <span className="text-[10px] text-ink-light bg-parchment-light px-2 py-0.5 rounded tracking-wide flex-1 truncate">{m.league_name}</span>
                <span className="text-[11px] text-ink-muted flex-shrink-0">{m.kickoff_time.slice(11, 16)}</span>
                {m.is_hot_match && <span className="text-[10px] font-semibold px-1.5 py-px bg-hot-bg text-moss rounded flex-shrink-0">热门</span>}
                {m.is_cold_match && <span className="text-[10px] font-semibold px-1.5 py-px bg-cold-bg text-amber rounded flex-shrink-0">冷门</span>}
              </div>
              {/* 对阵 */}
              <div className="flex items-center justify-between mb-3 gap-1.5">
                <span className="font-heading text-[15px] font-bold text-center flex-1 truncate leading-tight">{m.home_team}</span>
                <span className="text-[11px] text-ink-light flex-shrink-0 font-semibold">VS</span>
                <span className="font-heading text-[15px] font-bold text-center flex-1 truncate leading-tight">{m.away_team}</span>
              </div>
              {/* 进球数预测 — SNAP Top2（Model C 市场基线） */}
              <div className="mb-2.5 px-3 py-2.5 bg-parchment-light rounded-md">
                {(() => {
                  const snap = m.snap_top2_c;
                  if (!snap || snap.length < 2) return <span className="text-xs text-ink-light">未预测</span>;
                  return (
                    <div className="flex items-center justify-center gap-2">
                      <span className="w-11 h-11 rounded-md bg-moss text-white text-xl font-bold font-heading flex items-center justify-center shadow-sm">{snap[0]}</span>
                      <span className="text-xs text-ink-light">或</span>
                      <span className="w-11 h-11 rounded-md border-2 border-moss text-moss text-xl font-bold font-heading flex items-center justify-center">{snap[1]}</span>
                    </div>
                  );
                })()}
              </div>
              {/* 风险提示 — 固定高度 */}
              <div className="text-[10px] min-h-[22px] leading-relaxed">
                {m.risk_warning && m.risk_warning.length > 0 ? (
                  <span className="text-amber bg-cold-bg px-2 py-0.5 rounded">{m.risk_warning[0]}</span>
                ) : (
                  <span className="text-ink-light opacity-40 italic">暂无风险提示</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* 详情面板 */}
      {selectedMatchId !== null && (
        <div className="fixed inset-0 bg-ink/30 z-50 flex justify-end" onClick={closeDetail}>
          <div className="w-[580px] max-w-[100vw] h-full bg-white shadow-xl overflow-y-auto animate-[slideIn_0.25s_ease]" onClick={e => e.stopPropagation()}>
            <div className="sticky top-0 bg-white border-b border-border px-5 py-4 flex items-center justify-between z-10">
              <span className="font-heading text-base font-bold">
                {selectedMatch?.home_team} vs {selectedMatch?.away_team} · 详情
              </span>
              <button onClick={closeDetail} className="w-9 h-9 rounded-full border border-border flex items-center justify-center text-ink-muted hover:bg-parchment transition-colors">&times;</button>
            </div>
            {detailLoading ? (
              <div className="p-5 space-y-4">
                {[1,2,3,4].map(i => <div key={i} className="h-20 bg-parchment-light rounded animate-pulse" />)}
              </div>
            ) : (
              <div>
                {/* 预测摘要 */}
                {detail.prediction && (
                  <DetailSection title="预测数据">
                    <div className="flex gap-3 mb-3 flex-wrap">
                      {(["home", "draw", "away"] as const).map(k => {
                        const prob = k === "home" ? (detail.prediction!.home_prob ?? 0) : k === "draw" ? (detail.prediction!.draw_prob ?? 0) : (detail.prediction!.away_prob ?? 0);
                        return (
                          <div key={k} className="flex-1 min-w-[80px] text-center p-3 bg-parchment-light rounded-md">
                            <div className="text-[10px] text-ink-light">{k === "home" ? "主胜" : k === "draw" ? "平局" : "客胜"}</div>
                            <div className="text-xl font-bold font-heading" style={{ color: k === "home" ? "#2d5a3b" : k === "draw" ? "#8b7a3c" : "#8b5a2c" }}>{(prob * 100).toFixed(1)}%</div>
                          </div>
                        );
                      })}
                    </div>
                    <div className="flex gap-3">
                      <div className="flex-1 text-center p-3 bg-parchment-light rounded-md">
                        <div className="text-[10px] text-ink-light">预期进球 λ</div>
                        <div className="text-xl font-bold font-heading text-moss">{((detail.prediction.expected_goals_c ?? detail.prediction.expected_goals) ?? 0).toFixed(2)}</div>
                      </div>
                      <div className="flex-1 text-center p-3 bg-parchment-light rounded-md">
                        <div className="text-[10px] text-ink-light">大 2.5 概率</div>
                        <div className="text-xl font-bold font-heading text-moss">{((detail.prediction.over_2_5_prob ?? 0) * 100).toFixed(0)}%</div>
                      </div>
                    </div>
                  </DetailSection>
                )}
                {/* Model C 计算明细 */}
                {detail.prediction?.model_c_detail && (
                  <DetailSection title="Model C 计算明细">
                    <ModelCDetailTable detail={detail.prediction.model_c_detail} expected_goals_c={detail.prediction.expected_goals_c} />
                  </DetailSection>
                )}
                {/* H2H */}
                {detail.h2h.length > 0 && (
                  <DetailSection title="历史交锋">
                    <div className="font-body text-xs">
                      {detail.h2h.slice(0, 6).map((h: any, i: number) => {
                        const homeWin = h.home_score > h.away_score;
                        const awayWin = h.away_score > h.home_score;
                        const isDraw = h.home_score === h.away_score;
                        return (
                          <div key={i} className="py-1.5 border-b border-highlight last:border-0 hover:bg-highlight transition-colors">
                            <div className="text-ink-light mb-0.5">{h.date?.slice(0, 7)}</div>
                            <div className="flex items-center gap-1.5">
                              <span className={`flex-1 truncate text-right ${homeWin ? "text-red-600 font-bold" : isDraw ? "text-ink font-bold" : ""}`}>
                                {h.home_team || "主"}
                              </span>
                              <span className="font-bold text-xs mx-2 min-w-[28px] text-center">{h.home_score}:{h.away_score}</span>
                              <span className={`flex-1 truncate text-left ${awayWin ? "text-red-600 font-bold" : isDraw ? "text-ink font-bold" : ""}`}>
                                {h.away_team || "客"}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </DetailSection>
                )}
                {/* 球队对比 */}
                {detail.comparison && (
                  <DetailSection title="两队近期数据">
                    {(["home", "away"] as const).map(side => {
                      const t = detail.comparison![side];
                      if (!t) return null;
                      return <TeamRecentCard key={side} data={t} side={side} />;
                    })}
                  </DetailSection>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="px-5 py-4 border-b border-border">
      <div className="text-xs font-bold text-ink-light uppercase tracking-wide mb-3">{title}</div>
      {children}
    </div>
  );
}

// ==================== 球队近期战绩卡片 ====================
function TeamRecentCard({ data, side }: { data: any; side: "home" | "away" }) {
  const matches = data?.recent_matches || [];
  const stats = data?.stats || {};
  const wins = stats?.wins || 0;
  const draws = stats?.draws || 0;
  const losses = stats?.losses || 0;
  const total = wins + draws + losses;
  const homeStats = stats?.home || {};
  const awayStats = stats?.away || {};
  const colorMap: Record<string, string> = { W: "#2d5a3b", D: "#8b7e6a", L: "#c44b3c" };
  const teamName = data?.name || (side === "home" ? "主队" : "客队");

  const formatDate = (dateStr: string) => {
    if (!dateStr || dateStr.length < 10) return dateStr?.slice(5) || "";
    return dateStr.slice(2, 10);
  };

  const renderScore = (m: any) => {
    if (m.score == null || m.score === "") return "?:?";
    if (m.is_home == null) return m.score;
    const parts = m.score.split(":");
    const homeScore = m.is_home ? parts[0] : parts[1];
    const awayScore = m.is_home ? parts[1] : parts[0];
    return `${homeScore}:${awayScore}`;
  };

  const renderMatchupEl = (m: any) => {
    if (m.is_home == null) return <span>vs {m.opponent || "?"}</span>;
    const homeName = m.is_home ? teamName : (m.opponent || "?");
    const awayName = m.is_home ? (m.opponent || "?") : teamName;

    const resultStyle =
      m.result === "W" ? "text-red-600 font-bold" :
      m.result === "D" ? "text-ink font-bold" :
      m.result === "L" ? "text-blue-600 font-bold" : "";

    return (
      <>
        <span className={m.is_home ? resultStyle : ""}>{homeName}</span>
        <span className="text-ink-muted"> VS </span>
        <span className={m.is_home ? "" : resultStyle}>{awayName}</span>
      </>
    );
  };

  const hasHomeAwayData = matches.some((m: any) => m.is_home != null);

  return (
    <div className="mb-4 last:mb-0 p-3 bg-parchment-light rounded-md">
      <div className="font-heading font-bold text-sm mb-2">{teamName}（{side === "home" ? "主" : "客"}）</div>
      {total > 0 && (
        <div className="text-xs text-ink-muted mb-1">
          近{total}战 <span className="text-moss font-semibold">{wins}胜</span>{" "}
          <span className="text-ink-light">{draws}平</span>{" "}
          <span className="text-rust">{losses}负</span>
        </div>
      )}
      {hasHomeAwayData && (
        <div className="text-[11px] text-ink-muted mb-2 flex gap-4">
          <span>主场{homeStats.played || 0}场 <span className="text-moss font-semibold">{homeStats.wins || 0}胜</span> <span className="text-ink-light">{homeStats.draws || 0}平</span> <span className="text-rust">{homeStats.losses || 0}负</span></span>
          <span>客场{awayStats.played || 0}场 <span className="text-moss font-semibold">{awayStats.wins || 0}胜</span> <span className="text-ink-light">{awayStats.draws || 0}平</span> <span className="text-rust">{awayStats.losses || 0}负</span></span>
        </div>
      )}
      {matches.length > 0 ? (
        <div className="font-body text-xs space-y-0.5">
          {matches.map((m: any, i: number) => (
            <div key={i} className="flex items-center gap-1.5 py-0.5 border-b border-highlight last:border-0 hover:bg-highlight transition-colors px-1 -mx-1 rounded">
              <span className="w-4 h-4 rounded-full flex items-center justify-center text-[11px] font-bold text-white shrink-0"
                style={{ backgroundColor: colorMap[m.result] || "#d4c9b5" }}>{m.result || "?"}</span>
              <span className="text-ink-light w-16 shrink-0 font-mono text-[11px]">{formatDate(m.date)}</span>
              <span className="text-ink-muted truncate flex-1">{renderMatchupEl(m)}</span>
              <span className="font-semibold shrink-0 text-[11px]">{renderScore(m)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="font-body text-xs text-ink-light">暂无近期战绩</div>
      )}
    </div>
  );
}

// ==================== Model C 计算明细表 ====================
function ModelCDetailTable({ detail, expected_goals_c }: { detail: import("../api/client").ModelCDetail; expected_goals_c?: number }) {
  // null-safe 取值（部分历史预测记录可能缺少某些字段）
  const goal_line = detail.goal_line ?? 0;
  const calib = detail.calib ?? 0;
  const strength_adj = detail.strength_adj ?? 0;
  const form_adj = detail.form_adj ?? 0;
  const drop_adj = detail.drop_adj ?? 0;
  const lambda_raw = detail.lambda_raw ?? 0;
  const home_goals_avg = detail.home_goals_avg ?? 0;
  const away_goals_avg = detail.away_goals_avg ?? 0;
  const home_gf_avg_6 = detail.home_gf_avg_6 ?? 0;
  const away_gf_avg_6 = detail.away_gf_avg_6 ?? 0;
  const goal_drop = detail.goal_drop ?? 0;
  const low_score_applied = detail.low_score_applied ?? false;
  const low_score_factor = detail.low_score_factor ?? null;

  const formulaParts: string[] = [];
  formulaParts.push(`${goal_line.toFixed(1)} × ${calib.toFixed(3)}`);
  const adj = 1 + strength_adj + form_adj + drop_adj;
  formulaParts.push(`× ${adj.toFixed(4)}`);
  if (low_score_applied && low_score_factor != null) {
    formulaParts.push(`× ${low_score_factor}`);
  }

  return (
    <div className="space-y-2 text-xs">
      {/* 输入数据 */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
        <Row label="盘口基线" value={goal_line.toFixed(1)} />
        <Row label="联赛校准" value={calib.toFixed(3)} />
        <Row label="主队场均进球" value={home_goals_avg.toFixed(2)} />
        <Row label="客队场均进球" value={away_goals_avg.toFixed(2)} />
        <Row label="主队近6场进球" value={home_gf_avg_6.toFixed(2)} />
        <Row label="客队近6场进球" value={away_gf_avg_6.toFixed(2)} />
        <Row label="盘口回落" value={goal_drop.toFixed(2)} />
        <Row label="联赛" value={detail.league_name || "未知"} />
      </div>

      <div className="border-t border-parchment pt-2 mt-2">
        <div className="text-[10px] text-ink-light uppercase tracking-wide mb-1.5">调整项</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
          <Row label="攻防调整" value={strength_adj >= 0 ? `+${strength_adj.toFixed(4)}` : strength_adj.toFixed(4)}
            color={strength_adj > 0 ? "text-moss" : strength_adj < 0 ? "text-rust" : ""} />
          <Row label="状态调整" value={form_adj >= 0 ? `+${form_adj.toFixed(4)}` : form_adj.toFixed(4)}
            color={form_adj > 0 ? "text-moss" : form_adj < 0 ? "text-rust" : ""} />
          <Row label="盘口回落调整" value={drop_adj >= 0 ? `+${drop_adj.toFixed(4)}` : drop_adj.toFixed(4)}
            color={drop_adj < 0 ? "text-rust" : ""} />
          {low_score_applied && low_score_factor != null && (
            <Row label="低分规则" value={`×${low_score_factor}`} color="text-amber" />
          )}
        </div>
      </div>

      <div className="border-t border-parchment pt-2 mt-2">
        <div className="text-[10px] text-ink-light uppercase tracking-wide mb-1.5">计算公式</div>
        <div className="bg-parchment-light rounded px-3 py-2 font-mono text-[11px] text-ink break-all">
          λ = {formulaParts.join(" ")}<br />
          <span className="text-ink-muted">  = {lambda_raw.toFixed(4)}</span>
          {low_score_applied && low_score_factor != null && (
            <><br /><span className="text-amber">  → {low_score_factor} × {lambda_raw.toFixed(4)} = {(lambda_raw * low_score_factor).toFixed(4)}</span></>
          )}
          <br /><span className="text-ink-muted">  → cap [0.5, 6.0]</span>
          <br /><span className="text-moss font-bold">  = {expected_goals_c?.toFixed(2) ?? lambda_raw.toFixed(2)}</span>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between items-center py-0.5">
      <span className="text-ink-light">{label}</span>
      <span className={`font-mono font-semibold ${color || "text-ink"}`}>{value}</span>
    </div>
  );
}

// ==================== 历史报告 ====================
interface HistoryRow {
  date: string;
  league_name: string;
  home_team: string;
  away_team: string;
  expected_goals: number;         // Model B
  expected_goals_c?: number;      // Model C 市场基线
  snap_top2?: number[];           // Model B
  snap_top2_c?: number[];         // Model C 市场基线
  confidence_level: string;
  actual_score?: string;
  actual_total_goals?: number;
  result_goals?: number; // 1=hit, -1=miss, 0=pending
  kickoff_time: string;
}

function HistoryReportView() {
  const [range, setRange] = useState<RangeKey>("today");
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [summary, setSummary] = useState<{ total: number; settled: number; hit: number; miss: number }>({ total: 0, settled: 0, hit: 0, miss: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [repredictDate, setRepredictDate] = useState("");
  const [showConfirm, setShowConfirm] = useState(false);
  const [repredicting, setRepredicting] = useState(false);
  const [leagueAccuracy, setLeagueAccuracy] = useState<any[]>([]);
  const [leagueAccLoading, setLeagueAccLoading] = useState(false);
  const PAGE_SIZE = 20;

  // 筛选
  const [filterLeague, setFilterLeague] = useState("all");
  const [filterResult, setFilterResult] = useState("all");
  const toast = useToast();

  const yesterday = useMemo(() => {
    const d = new Date(); d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
  }, []);

  // 拉取历史数据（单次范围查询）
  const fetchHistory = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // 有指定日期时用指定日期，否则用 range 计算
      let date_from: string, date_to: string;
      if (repredictDate) {
        date_from = repredictDate;
        date_to = repredictDate;
      } else {
        const days = getRangeDays(range);
        const dates = getDateRange(days);
        date_to = dates[0];
        date_from = dates[dates.length - 1];
      }

      const res = await getReportRange({ date_from, date_to });
      const rawData: any[] = res.data || [];
      const apiSummary = res.summary;

      const allRows: HistoryRow[] = rawData.map((m: any) => ({
        date: (m.kickoff_time || "").slice(0, 10),
        league_name: m.league_name || "",
        home_team: m.home_team || "",
        away_team: m.away_team || "",
        expected_goals: m.expected_goals || 0,
        expected_goals_c: m.expected_goals_c,
        snap_top2: m.snap_top2,
        snap_top2_c: m.snap_top2_c,
        confidence_level: m.confidence_level || "low",
        actual_score: m.actual_score,
        actual_total_goals: m.actual_total_goals,
        result_goals: m.result_goals,
        kickoff_time: m.kickoff_time || "",
      }));

      setRows(allRows);
      setSummary({
        total: apiSummary?.total ?? allRows.length,
        settled: apiSummary?.settled ?? 0,
        hit: apiSummary?.goals_hit ?? 0,
        miss: apiSummary?.goals_miss ?? 0,
      });
    } catch {
      setError("历史数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [range, repredictDate]);

  useEffect(() => { fetchHistory(); setPage(1); }, [range]); // eslint-disable-line react-hooks/exhaustive-deps

  // 拉取联赛命中率（近30天固定）
  useEffect(() => {
    setLeagueAccLoading(true);
    getLeagueAccuracy(30)
      .then((res) => setLeagueAccuracy(res.data || []))
      .catch(() => {})
      .finally(() => setLeagueAccLoading(false));
  }, [range]);

  // 筛选
  const filteredRows = useMemo(() => {
    let list = rows;
    if (filterLeague !== "all") list = list.filter(r => r.league_name === filterLeague);
    if (filterResult === "hit") list = list.filter(r => r.result_goals === 1);
    if (filterResult === "miss") list = list.filter(r => r.result_goals === -1);
    if (filterResult === "pending") list = list.filter(r => r.result_goals === 0 || r.result_goals == null);
    return list;
  }, [rows, filterLeague, filterResult]);

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const pagedRows = filteredRows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  // 联赛列表
  const leagueOptions = useMemo(() => {
    const set = new Set(rows.map(r => r.league_name).filter(Boolean));
    return Array.from(set).sort();
  }, [rows]);

  const hitRate = summary.settled > 0 ? ((summary.hit / summary.settled) * 100).toFixed(1) : "0.0";

  const handleRepredict = async () => {
    const date = repredictDate || yesterday;
    setRepredicting(true);
    try {
      const res = await repredictModelB(date);
      toast.toast(res.message || `比赛日 ${date} Model B 重预测完成`, "success");
      setShowConfirm(false);
      fetchHistory();
    } catch {
      toast.toast(`比赛日 ${date} 重预测失败`, "error");
    } finally {
      setRepredicting(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="h-10 bg-white rounded animate-pulse" />
        <div className="grid grid-cols-4 gap-3">
          {[1,2,3,4].map(i => <div key={i} className="h-24 bg-white rounded-lg border border-border animate-pulse" />)}
        </div>
        <div className="h-64 bg-white rounded-lg border border-border animate-pulse" />
        <div className="h-64 bg-white rounded-lg border border-border animate-pulse" />
      </div>
    );
  }

  if (error) return <ErrorState message={error} onRetry={fetchHistory} />;

  return (
    <div className="space-y-4">
      {/* 时间维度 + 重新预测 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-0.5 bg-white border border-border rounded p-1">
          {(Object.entries(RANGE_LABELS) as [RangeKey, string][]).map(([key, label]) => (
            <span key={key}
              className={`px-3 py-1 text-xs rounded cursor-pointer transition-colors ${range === key ? "bg-ink text-white" : "text-ink-muted"}`}
              onClick={() => { setRange(key); setRepredictDate(""); }}>{label}</span>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-ink-light">比赛日</span>
          <input type="date" value={repredictDate || yesterday}
            onChange={e => setRepredictDate(e.target.value)}
            className="px-2.5 py-1.5 text-xs border border-border rounded bg-white text-ink outline-none focus:border-moss" />
          <span className="text-[10px] text-ink-light">12:00~次日</span>
          <button onClick={() => setShowConfirm(true)}
            className="px-3 py-1.5 text-xs bg-rust text-white rounded hover:opacity-90 transition-opacity font-body">
            {repredicting ? "预测中..." : "重新预测"}
          </button>
          <button onClick={() => { setPage(1); fetchHistory(); }}
            className="px-3 py-1.5 text-xs text-ink-muted border border-border rounded hover:border-moss transition-colors">🔄 刷新</button>
        </div>
      </div>

      {/* 确认弹窗 */}
      {showConfirm && (
        <div className="fixed inset-0 bg-ink/30 z-50 flex items-center justify-center" onClick={() => setShowConfirm(false)}>
          <div className="bg-white rounded-lg p-6 w-[440px] max-w-[90vw] shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="text-base font-bold font-heading mb-2">确认重新预测</div>
            <div className="text-xs text-ink-muted leading-relaxed mb-5">
              <p>将对比赛日 <strong className="text-ink">{repredictDate || yesterday}</strong> 的所有已结束比赛重新执行 Model B（Poisson 回归）进球数预测。</p>
              <ul className="mt-2 ml-4 space-y-1 text-[11px] text-ink-light">
                <li>使用最新模型版本重新计算预期进球 λ</li>
                <li>更新 SNAP Top2、进球分布、比分 Top5</li>
                <li><strong>自动保留旧预测数据供对比</strong></li>
                <li>不影响模型 A（胜平负/让球）的预测结果</li>
              </ul>
            </div>
            <div className="flex justify-end gap-2.5">
              <button onClick={() => setShowConfirm(false)} className="px-4 py-2 text-xs border border-border rounded text-ink-muted hover:bg-parchment">取消</button>
              <button onClick={handleRepredict} disabled={repredicting} className="px-4 py-2 text-xs bg-rust text-white rounded hover:opacity-90 disabled:opacity-50">确认重新预测</button>
            </div>
          </div>
        </div>
      )}

      {/* 统计卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "进球 SNAP Top2 命中率", val: `${hitRate}%`, color: "text-moss" },
          { label: "累计预测场次", val: String(summary.total), color: "text-ink" },
          { label: "已结算", val: String(summary.settled), color: "text-ink" },
          { label: "命中 / 未命中", val: `${summary.hit} / ${summary.miss}`, color: summary.hit >= summary.miss ? "text-moss" : "text-rust" },
        ].map(s => (
          <div key={s.label} className="bg-white rounded-lg border border-border p-4 text-center">
            <div className="text-[10px] text-ink-light tracking-wide mb-1">{s.label}</div>
            <div className={`text-2xl font-bold font-heading ${s.color}`}>{s.val}</div>
            <div className="text-[10px] text-ink-muted mt-1">{RANGE_LABELS[range]}</div>
          </div>
        ))}
      </div>

      {/* 联赛命中率柱状图 */}
      <LeagueAccuracyBarChart data={leagueAccuracy} loading={leagueAccLoading} />

      {/* 空数据 */}
      {rows.length === 0 && (
        <EmptyState icon="📋" message="暂无历史预测数据"
          description="所选时间范围内没有已完成的预测记录，请调整筛选条件" />
      )}

      {/* 历史表格 */}
      {rows.length > 0 && (
        <div className="bg-white rounded-lg border border-border overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="text-sm font-bold font-heading">历史预测记录</span>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-ink-light">联赛</span>
              <select value={filterLeague} onChange={e => { setFilterLeague(e.target.value); setPage(1); }}
                className="text-[11px] border border-border rounded px-2 py-1 bg-white text-ink outline-none">
                <option value="all">全部</option>
                {leagueOptions.map(l => <option key={l} value={l}>{l}</option>)}
              </select>
              <span className="text-[10px] text-ink-light">命中</span>
              <select value={filterResult} onChange={e => { setFilterResult(e.target.value); setPage(1); }}
                className="text-[11px] border border-border rounded px-2 py-1 bg-white text-ink outline-none">
                <option value="all">全部</option>
                <option value="hit">命中</option>
                <option value="miss">未命中</option>
                <option value="pending">待结算</option>
              </select>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-parchment-light text-ink-light">
                  <th className="text-left px-3 py-2.5 font-semibold text-[10px]">日期</th>
                  <th className="text-left px-3 py-2.5 font-semibold text-[10px]">联赛</th>
                  <th className="text-left px-3 py-2.5 font-semibold text-[10px]">对阵</th>
                  <th className="text-left px-3 py-2.5 font-semibold text-[10px]">SNAP Top2</th>
                  <th className="text-left px-3 py-2.5 font-semibold text-[10px]">λ (C)</th>
                  <th className="text-left px-3 py-2.5 font-semibold text-[10px]">置信度</th>
                  <th className="text-left px-3 py-2.5 font-semibold text-[10px]">实际比分</th>
                  <th className="text-left px-3 py-2.5 font-semibold text-[10px]">实际进球</th>
                  <th className="text-left px-3 py-2.5 font-semibold text-[10px]">命中</th>
                </tr>
              </thead>
              <tbody>
                {pagedRows.map((r, i) => {
                  const snap = r.snap_top2_c?.length === 2
                    ? `${r.snap_top2_c[0]}/${r.snap_top2_c[1]}`
                    : "-";
                  // 基于 Model C SNAP 计算命中
                  const cGoalsHit = r.actual_total_goals != null && r.snap_top2_c?.length === 2
                    ? r.snap_top2_c.includes(Math.min(r.actual_total_goals, 4))
                    : null; // null = 无Model C数据或无赛果
                  return (
                    <tr key={i} className="border-t border-parchment hover:bg-highlight">
                      <td className="px-3 py-2.5 text-ink-muted">{r.date} {r.kickoff_time.slice(11, 16)}</td>
                      <td className="px-3 py-2.5">{r.league_name}</td>
                      <td className="px-3 py-2.5 font-semibold text-ink">{r.home_team} vs {r.away_team}</td>
                      <td className={`px-3 py-2.5 font-bold ${cGoalsHit === true ? "text-red-600" : "text-moss"}`}>{snap}</td>
                      <td className="px-3 py-2.5">{r.expected_goals_c != null ? r.expected_goals_c.toFixed(1) : "-"}</td>
                      <td className="px-3 py-2.5"><span className={confidenceTextColor(r.confidence_level)}>{confidencePercent(r.confidence_level)}%</span></td>
                      <td className="px-3 py-2.5 font-heading font-bold">{r.actual_score || "-"}</td>
                      <td className="px-3 py-2.5 font-semibold">{r.actual_total_goals ?? "-"}</td>
                      <td className="px-3 py-2.5">
                        {cGoalsHit === true ? <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-hot-bg text-moss">命中</span>
                          : cGoalsHit === false ? <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-cold-bg text-rust">未命中</span>
                            : <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-parchment text-ink-light">待结算</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {/* 分页 */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-1.5 py-3 border-t border-border">
              <button disabled={page <= 1} onClick={() => setPage(1)}
                className="w-7 h-7 flex items-center justify-center text-xs border border-border rounded disabled:opacity-30">«</button>
              <button disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}
                className="w-7 h-7 flex items-center justify-center text-xs border border-border rounded disabled:opacity-30">‹</button>
              {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                const pn = totalPages <= 7 ? i + 1 : page <= 4 ? i + 1 : page >= totalPages - 3 ? totalPages - 6 + i : page - 3 + i;
                return <button key={pn} onClick={() => setPage(pn)}
                  className={`w-7 h-7 flex items-center justify-center text-xs rounded ${pn === page ? "bg-moss text-white font-semibold" : "text-ink-muted border border-border cursor-pointer"}`}>{pn}</button>;
              })}
              <button disabled={page >= totalPages} onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                className="w-7 h-7 flex items-center justify-center text-xs border border-border rounded disabled:opacity-30">›</button>
              <button disabled={page >= totalPages} onClick={() => setPage(totalPages)}
                className="w-7 h-7 flex items-center justify-center text-xs border border-border rounded disabled:opacity-30">»</button>
              <span className="text-[10px] text-ink-muted ml-3">共 {filteredRows.length} 条</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
