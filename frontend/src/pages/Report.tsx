import { useState, useEffect, useCallback } from "react";
import { getDailyReport, getDailySummary, updateResults, repredictModelB } from "../api/client";
import ErrorState from "../components/ErrorState";
import { useToast } from "../components/Toast";

function toDateStr(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function getYesterdayStr(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return toDateStr(d);
}

function fmtDateCN(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  return d.toLocaleDateString("zh-CN");
}

function shiftDate(dateStr: string, days: number): string {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + days);
  return toDateStr(d);
}

function fmtProb(p: number | null | undefined): string {
  if (p == null) return "-";
  return (p * 100).toFixed(1) + "%";
}

function getTopScores(scores: Array<{ score: string; prob: number }> | null | undefined, n: number = 3) {
  if (!scores || scores.length === 0) return [];
  return scores.slice(0, n);
}

const DIR_LABEL: Record<string, string> = { home: "主胜", draw: "平局", away: "客胜" };

export default function Report() {
  const { toast } = useToast();
  const [summary, setSummary] = useState<any>({});
  const [report, setReport] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);
  const [selectedDate, setSelectedDate] = useState<string>(getYesterdayStr());
  // 本次点击「更新赛果」实际更新的比赛 ID（用于醒目标记）
  const [updatedIds, setUpdatedIds] = useState<Set<number>>(new Set());

  const fetchData = useCallback((date: string) => {
    setLoading(true);
    setError(null);
    Promise.all([getDailySummary(date), getDailyReport(date)]).then(([s, r]) => {
      setSummary(s.data || {});
      setReport(r.data || []);
    }).catch(() => {
      setError("加载失败");
    }).finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchData(selectedDate); }, [fetchData, selectedDate]);

  const goDate = (days: number) => {
    setSelectedDate(prev => shiftDate(prev, days));
  };

  const handleUpdateResults = async () => {
    setUpdating(true);
    try {
      const res = await updateResults(selectedDate);
      toast(res.message || "更新完成", res.success ? "success" : "error");
      if (res.success) {
        setUpdatedIds(new Set(res.updated_ids || []));
        fetchData(selectedDate);
      }
    } catch {
      toast("赛果更新失败", "error");
    } finally {
      setUpdating(false);
    }
  };

  const handleRepredictModelB = async () => {
    setUpdating(true);
    try {
      const res = await repredictModelB(selectedDate);
      toast(res.message || "Model B 重预测完成", "success");
      fetchData(selectedDate);
    } catch {
      toast("Model B 重预测失败", "error");
    } finally {
      setUpdating(false);
    }
  };

  const coldMatches = report.filter((m: any) => m.is_cold_match);
  const hotMatches = report.filter((m: any) => m.is_hot_match);
  const displayDate = summary.display_date || selectedDate;
  const isToday = selectedDate === getYesterdayStr();

  if (error) return <ErrorState message={error} onRetry={() => fetchData(selectedDate)} />;

  return (
    <div className="bg-parchment min-h-screen">
      {loading ? (
        <div className="bg-parchment">
          <div className="flex items-center justify-between px-5 py-3 bg-parchment-dark border-b border-border-dark">
            <span className="font-heading text-base font-bold tracking-wider">每日预测报告</span>
          </div>
          <div className="flex gap-3 px-5 py-3.5 border-b border-border">
            {[1,2,3,4].map(i => (
              <div key={i} className="flex-1 bg-white rounded-md border border-border p-3 animate-pulse">
                <div className="w-12 h-2 bg-border rounded mx-auto mb-2" />
                <div className="w-12 h-5 bg-border rounded mx-auto" />
              </div>
            ))}
          </div>
          <div className="px-5 py-3.5 space-y-2">
            <div className="w-24 h-4 bg-border rounded animate-pulse mb-3" />
            {[1,2,3].map(i => (
              <div key={i} className="bg-white rounded-md border border-border p-3 animate-pulse">
                <div className="w-48 h-3 bg-border rounded mb-2" />
                <div className="w-64 h-2 bg-border rounded mb-1" />
                <div className="w-52 h-2 bg-border rounded" />
              </div>
            ))}
          </div>
        </div>
      ) : (
        <>
          {/* 顶部导航 */}
          <div className="flex items-center justify-between px-5 py-3 bg-parchment-dark border-b border-border-dark">
            <span className="font-heading text-base font-bold tracking-wider">每日预测报告</span>
            <div className="flex items-center gap-2">
              {/* 日期切换 */}
              <div className="flex items-center gap-1 bg-white border border-border rounded-md px-1.5 py-0.5">
                <button
                  onClick={() => goDate(-1)}
                  className="text-ink-muted hover:text-moss px-1 text-sm leading-none transition-colors"
                  title="前一天"
                >
                  ◀
                </button>
                <input
                  type="date"
                  value={selectedDate}
                  onChange={(e) => setSelectedDate(e.target.value)}
                  max={getYesterdayStr()}
                  className="font-mono text-xs text-ink bg-transparent border-none outline-none px-1 py-0.5 w-[120px] text-center [color-scheme:light]"
                />
                <button
                  onClick={() => goDate(1)}
                  disabled={isToday}
                  className="text-ink-muted hover:text-moss px-1 text-sm leading-none transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                  title="后一天"
                >
                  ▶
                </button>
              </div>
              <span className="font-body text-xs text-ink-muted">{fmtDateCN(displayDate)}</span>
              <button
                onClick={handleUpdateResults}
                disabled={updating}
                className="font-body text-[11px] text-white bg-red-600 hover:bg-red-700 border border-red-600 rounded px-2.5 py-1 transition-colors disabled:opacity-50"
              >
                {updating ? "更新中..." : "更新赛果"}
              </button>
              <button
                onClick={() => fetchData(selectedDate)}
                disabled={loading}
                className="font-body text-[11px] text-ink-muted hover:text-moss border border-border rounded px-2.5 py-1 transition-colors disabled:opacity-50"
              >
                刷新
              </button>
              <button
                onClick={handleRepredictModelB}
                disabled={updating}
                className="font-body text-[11px] text-white bg-moss hover:bg-moss-dark border border-moss rounded px-2.5 py-1 transition-colors disabled:opacity-50"
              >
                {updating ? "预测中..." : "Model B 重预测"}
              </button>
            </div>
          </div>

          {/* 概览卡片 */}
          <div className="flex gap-3 px-5 py-3.5 border-b border-border">
            {[
              {label:"开售赛事", value: summary.total_matches || 0, unit:"场"},
              {label:"冷门预警", value: summary.cold_match_count || 0, unit:"场", color:"text-amber"},
              {label:"已结算", value: summary.settled_count || 0, unit:"场", color: (summary.settled_count > 0) ? "text-red-600" : ""},
              {label:"胜平负命中", value: summary.spf_hit != null ? `${summary.spf_hit}/${summary.settled_count}` : "-", unit:"", color: summary.spf_hit > 0 ? "text-red-600" : ""},
              {label:"让球命中", value: summary.hcp_hit != null ? `${summary.hcp_hit}/${summary.settled_count}` : "-", unit:"", color: summary.hcp_hit > 0 ? "text-red-600" : ""},
              {label:"进球命中", value: summary.goals_hit != null ? `${summary.goals_hit}/${summary.settled_count}` : "-", unit:"", color: summary.goals_hit > 0 ? "text-red-600" : ""},
              {label:"模型版本", value: summary.model_version || "-", unit:"", textSm: true},
            ].map((item, i) => (
              <div key={i} className="flex-1 bg-white rounded-md border border-border p-2.5 text-center font-body">
                <div className="text-[10px] text-ink-light tracking-wide">{item.label}</div>
                <div className={`text-lg font-bold mt-0.5 ${item.color || "text-ink"} ${item.textSm ? "text-xs" : ""}`}>{item.value}</div>
                <div className="text-[10px] text-ink-muted">{item.unit}</div>
              </div>
            ))}
          </div>

          {/* 冷热提示 */}
          <div className="px-5 py-3.5 border-b border-border">
            <div className="flex items-center gap-2 mb-2.5">
              <span className="text-sm font-bold">冷热赛事提示</span>
            </div>
            {coldMatches.length === 0 && hotMatches.length === 0 ? (
              <div className="text-center py-4 text-ink-light font-body text-xs">暂无冷门预警或热门推荐赛事</div>
            ) : (
              <div className="flex gap-2.5 font-body text-[11px]">
                {coldMatches.slice(0,2).map((m: any) => (
                  <div key={m.id} className="flex-1 bg-white border-l-2 border-amber rounded-r-md p-2.5 border border-l-2">
                    <div className="flex items-center gap-1.5">
                      <span className="bg-cold-bg text-amber px-1.5 py-0.5 rounded-sm font-bold text-sm">冷</span>
                      <span className="font-semibold text-ink">{m.home_team} vs {m.away_team}</span>
                    </div>
                    <div className="mt-1 text-ink-muted">建议观望</div>
                  </div>
                ))}
                {hotMatches.slice(0,2).map((m: any) => (
                  <div key={m.id} className="flex-1 bg-white border-l-2 border-moss rounded-r-md p-2.5 border border-l-2">
                    <div className="flex items-center gap-1.5">
                      <span className="bg-hot-bg text-moss px-1.5 py-0.5 rounded-sm font-bold text-sm">热</span>
                      <span className="font-semibold text-ink">{m.home_team} vs {m.away_team}</span>
                    </div>
                    <div className="mt-1 text-ink-muted">高置信度</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 预测清单 */}
          <div className="px-5 py-3.5">
            <div className="text-sm font-bold mb-2.5">预测结果清单</div>
            {report.length === 0 ? (
              <div className="p-6 text-center text-ink-light bg-white rounded-md border border-border">
                暂无 {fmtDateCN(displayDate)} 的预测数据，请检查赛程同步状态
              </div>
            ) : (
              <div className="space-y-2">
                {report.map((m: any) => {
                  // snap_top2 由后端计算并存储，前端直接读取
                  const top2goals: number[] = m.snap_top2 && m.snap_top2.length >= 2
                    ? m.snap_top2.slice(0, 2)
                    : (() => {
                        // 兼容旧数据（无 snap_top2 字段时回退计算）
                        const eg = m.expected_goals || 0;
                        const frac = eg - Math.floor(eg);
                        let effective = eg;
                        if (frac < 0.10) effective = Math.floor(eg);
                        else if (frac > 0.90) effective = Math.ceil(eg);
                        const dists = [0,1,2,3,4,5,6].map(i => ({i, d: Math.abs(effective - i)})).sort((a,b) => a.d - b.d);
                        return dists.slice(0, 2).map(d => d.i);
                      })();
                  const topScores = getTopScores(m.score_top5_json, 3);
                  const hasActual = m.actual_score != null;
                  const spfHit = hasActual && m.result_spf === 1;
                  const hcpHit = hasActual && m.result_hcp === 1;
                  const goalsHit = hasActual && m.result_goals === 1;
                  const scoreHit = hasActual && m.result_score === 1;
                  // 已更新赛果（result_spf 为 1/-1）；本次点击「更新赛果」刚结算的场次额外醒目标记
                  const isUpdated = m.result_spf != null && m.result_spf !== 0;
                  const justUpdated = updatedIds.has(m.id);

                  return (
                    <div
                      key={m.id}
                      className={`bg-white rounded-md border border-border overflow-hidden ${justUpdated ? "ring-1 ring-red-600" : ""}`}
                    >
                      {/* 头部行：时间 | 联赛 | 对阵 | 赛果 | 标签 */}
                      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-highlight">
                        <span className="font-mono text-[10px] text-ink-muted min-w-[34px]">
                          {m.kickoff_time?.slice(11, 16)}
                        </span>
                        <span className="text-[10px] text-ink-light bg-parchment-dark px-1 py-0.5 rounded shrink-0">
                          {m.league_name}
                        </span>
                        <span className="font-semibold text-xs text-ink truncate min-w-0">
                          {m.home_team} vs {m.away_team}
                        </span>
                        {/* 赛果内嵌 */}
                        {hasActual && (
                          <span className="font-bold text-red-600 text-xs shrink-0 ml-1">
                            {m.actual_home_score}:{m.actual_away_score}
                          </span>
                        )}
                        {/* 标签 */}
                        <span className="flex items-center gap-1 shrink-0 ml-auto">
                          {isUpdated && (
                            <span
                              className={`px-1 py-0.5 rounded-sm text-[10px] font-bold ${
                                justUpdated
                                  ? "bg-red-600 text-white"
                                  : "bg-red-50 text-red-600 border border-red-600"
                              }`}
                              title={justUpdated ? "本次点击「更新赛果」已结算" : "该场比赛赛果已更新"}
                            >
                              {justUpdated ? "已更新" : "已结算"}
                            </span>
                          )}
                          {m.is_cold_match && (
                            <span className="bg-cold-bg text-amber px-1 py-0.5 rounded-sm text-[10px] font-bold">
                              冷
                            </span>
                          )}
                          {m.is_hot_match && (
                            <span className="bg-hot-bg text-moss px-1 py-0.5 rounded-sm text-[10px] font-bold">
                              热
                            </span>
                          )}
                        </span>
                      </div>

                      {/* 数据行：两列网格 */}
                      <div className="px-3 py-1.5 font-body text-[11px] space-y-0.5">
                        {/* Row 1: 胜平负 | 让球 */}
                        <div className="flex items-center gap-3">
                          <div className="flex items-center gap-0.5 min-w-0">
                            <span className="text-ink-muted text-[10px] shrink-0">胜平负</span>
                            <ProbItem label="主" prob={m.home_prob} active={m.spf_direction === "home"} hit={spfHit && m.spf_direction === "home"} />
                            <ProbItem label="平" prob={m.draw_prob} active={m.spf_direction === "draw"} hit={spfHit && m.spf_direction === "draw"} />
                            <ProbItem label="客" prob={m.away_prob} active={m.spf_direction === "away"} hit={spfHit && m.spf_direction === "away"} />
                            <span className="text-ink-muted text-[10px] ml-0.5">→{DIR_LABEL[m.spf_direction] || ""}</span>
                          </div>
                          <span className="text-border-dark text-[10px] shrink-0">|</span>
                          <div className="flex items-center gap-0.5 min-w-0">
                            <span className="text-ink-muted text-[10px] shrink-0">让球</span>
                            <ProbItem label="主" prob={m.handicap_home_prob} active={m.hcp_direction === "home"} hit={hcpHit && m.hcp_direction === "home"} />
                            <ProbItem label="平" prob={m.handicap_draw_prob} active={m.hcp_direction === "draw"} hit={hcpHit && m.hcp_direction === "draw"} />
                            <ProbItem label="客" prob={m.handicap_away_prob} active={m.hcp_direction === "away"} hit={hcpHit && m.hcp_direction === "away"} />
                            <span className="text-ink-muted text-[10px] ml-0.5">→{DIR_LABEL[m.hcp_direction] || ""}</span>
                          </div>
                        </div>

                        {/* Row 2: 进球 | 比分 */}
                        <div className="flex items-center gap-3">
                          <div className="flex items-center gap-0.5 min-w-0">
                            <span className="text-ink-muted text-[10px] shrink-0">进球</span>
                            <span className="text-ink-muted text-[10px]">γ={(m.expected_goals||0).toFixed(1)}</span>
                            <span className={`font-semibold ${goalsHit ? "text-red-600" : "text-ink"}`}>
                              {top2goals[0] >= 6 ? '6+' : top2goals[0]}球/{top2goals[1] >= 6 ? '6+' : top2goals[1]}球
                            </span>
                            {/* 进球极端预警：极低或极高预期 */}
                            {((m.expected_goals||0) < 1.0 || (m.expected_goals||0) > 5.0) && (
                              <span className="text-amber text-[10px] font-bold ml-0.5" title="进球数预期处于极端区间，不确定性较高">
                                ⚠
                              </span>
                            )}
                          </div>
                          <span className="text-border-dark text-[10px] shrink-0">|</span>
                          <div className="flex items-center gap-0.5 min-w-0">
                            <span className="text-ink-muted text-[10px] shrink-0">比分</span>
                            {topScores.length > 0 ? (
                              topScores.map((s: any, i: number) => {
                                const isHit = scoreHit && s.score === m.actual_score;
                                return (
                                  <span key={i} className={`font-semibold ${isHit ? "text-red-600" : "text-ink"}`}>
                                    {s.score}
                                  </span>
                                );
                              })
                            ) : <span className="text-ink-light">-</span>}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="flex justify-end gap-2.5 mt-3 font-body">
              <button onClick={() => toast("摘要已复制到剪贴板", "success")} className="bg-white border border-border-dark text-ink px-4 py-1.5 rounded text-[11px] hover:border-moss transition-colors">复制摘要</button>
              <button onClick={() => toast("CSV 导出已开始", "info")} className="bg-white border border-border-dark text-ink px-4 py-1.5 rounded text-[11px] hover:border-moss transition-colors">导出 CSV</button>
              <button onClick={() => toast("Excel 导出已开始", "info")} className="bg-white border border-border-dark text-ink px-4 py-1.5 rounded text-[11px] hover:border-moss transition-colors">导出 Excel</button>
              <button onClick={() => toast("PDF 报告生成中...", "info")} className="bg-moss border border-moss text-white px-4 py-1.5 rounded text-[11px] hover:bg-moss-dark transition-colors">导出 PDF 报告</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/** 单个概率项，预测方向高亮，命中时红色加粗 */
function ProbItem({ label, prob, active, hit }: { label: string; prob: number | null; active: boolean; hit?: boolean }) {
  const colorClass = hit
    ? "font-bold text-red-600"
    : active
      ? "bg-amber/10 text-amber"
      : "text-ink";
  return (
    <span className="ml-1.5 flex items-baseline gap-0.5">
      <span className="text-ink-light text-[10px]">{label}</span>
      <span className={`font-semibold text-xs rounded px-1 ${colorClass}`}>
        {fmtProb(prob)}
      </span>
    </span>
  );
}
